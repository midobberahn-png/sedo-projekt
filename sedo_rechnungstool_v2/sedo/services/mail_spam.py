"""Conservative local rules. Mail content and provider headers are untrusted data.

No DNS/URL requests, model calls, deletion, or implicit sender rules. The account
lease serializes settings, manual corrections, sync and remote mutations.
"""
import hashlib
import ipaddress
import re
import time
from email.utils import getaddresses
from html.parser import HTMLParser
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import F, Q
from django.utils import timezone

from sedo.models import AuditLog, MailLocation, MailMessage, MailSpamMove
from .mail_mime import body_and_attachments, parse
from .mail_secrets import MailError
from .mail_storage import original_bytes

BLOCKED = ('suspect', 'spam')
BATCH = 25


def audit(message, text, user=None):
    AuditLog.objects.create(user=user, action='update', object_type='MailSpam',
        object_id=str(message.pk), object_repr=f'Nachricht {message.pk}', message=text,
        metadata={'account': message.account_id})


def normalize_rule(kind, value):
    value = value.strip().lower()
    if kind == 'address':
        try:
            validate_email(value)
            value.encode('ascii')
        except (ValidationError, UnicodeError):
            raise MailError('Bitte eine einzelne gültige Absenderadresse eingeben.')
    elif kind == 'domain':
        try:
            value = value.encode('idna').decode('ascii')
        except UnicodeError:
            raise MailError('Bitte eine gültige Domain ohne @, Sternchen oder Internetadresse eingeben.')
        if not re.fullmatch(r'(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', value):
            raise MailError('Bitte eine genaue Domain wie beispiel.de eingeben; keine Unterdomains oder Platzhalter ergänzen.')
    else:
        raise MailError('Unbekannte Regelart.')
    return value


def sender_address(mime):
    values = mime.get_all('From', [])
    if len(values) != 1 or getattr(values[0], 'defects', ()):
        return ''
    pairs = getaddresses([str(values[0])])
    if len(pairs) != 1:
        return ''
    try:
        return normalize_rule('address', pairs[0][1])
    except MailError:
        return ''


def matching_rule(account, address):
    if not address:
        return None
    rules = account.spam_rules.filter(Q(kind='address', value=address) |
        Q(kind='domain', value=address.rsplit('@', 1)[-1]))
    # An exact address always wins over an exact domain; no suffix matching.
    return rules.filter(kind='address').first() or rules.filter(kind='domain').first()


class LinkHints(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.href, self.label, self.signals = '', [], set()

    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href, self.label = dict(attrs).get('href', '') or '', []

    def handle_data(self, data):
        if self.href:
            self.label.append(data)

    def handle_endtag(self, tag):
        if tag != 'a' or not self.href:
            return
        try:
            target = urlsplit(self.href)
            if target.scheme.lower() in ('http', 'https') and target.hostname:
                if target.username:
                    self.signals.add('Verschleiertes Linkziel')
                try:
                    ipaddress.ip_address(target.hostname)
                    self.signals.add('Link auf eine IP-Adresse')
                except ValueError:
                    pass
                label = ''.join(self.label).strip()
                if re.match(r'^https?://', label, re.I):
                    shown = urlsplit(label).hostname
                    if shown and shown.lower().removeprefix('www.') != target.hostname.lower().removeprefix('www.'):
                        self.signals.add('Linktext und Ziel stimmen nicht überein')
        except ValueError:
            pass
        self.href, self.label = '', []


def evaluate(message, *, review_old=False):
    """Pure preview: no write or network operation. Return state, reason, rule."""
    if message.spam_manual == 'spam':
        return 'spam', 'Manuell als Spam markiert', None
    if message.locations.filter(folder__role='junk', folder__active=True).exists():
        return 'spam', 'Im Spamordner des Mailservers', None
    if message.spam_manual == 'ham':
        return 'clear', 'Manuell als kein Spam freigegeben', None
    account = message.account
    if account.spam_mode == 'off':
        return 'clear', 'Lokale Filterprüfung ausgeschaltet', None
    if not review_old and message.created_at < account.spam_started_at:
        return 'clear', 'Altbestand – bei Bedarf gezielt prüfen', None
    if message.content_error:
        return 'suspect', 'Nachricht unvollständig – manuell prüfen', None
    try:
        raw = original_bytes(message)
        if hashlib.sha256(raw).hexdigest() != message.digest:
            raise MailError('Prüfsumme stimmt nicht.')
        mime = parse(raw)
        address = sender_address(mime)
        if not address:
            return 'suspect', 'Absenderadresse fehlt oder ist mehrdeutig', None
        rule = matching_rule(account, address)
        if rule:
            return ('spam' if rule.action == 'block' else 'clear',
                    ('Sperrregel: ' if rule.action == 'block' else 'Freigaberegel: ') + rule.value, rule)
        signals = set()
        if any(re.search(r'\.(exe|scr|js|vbs|bat|cmd|ps1|lnk|com|msi)$', item.get('name', ''), re.I)
               for item in message.attachments):
            signals.add('Ausführbarer Anhang')
        if re.search(r'(passwort|password|zugangsdaten|konto).{0,60}(bestätigen|verifizieren|verify|gesperrt)|'
                     r'(dringend|urgent|sofort).{0,60}(anmelden|login|klicken|click)',
                     (message.subject + '\n' + message.body)[:100000], re.I | re.S):
            signals.add('Aufforderung zur dringenden Anmeldung oder Kontoprüfung')
        _, html, _ = body_and_attachments(mime)
        links = LinkHints()
        links.feed(html[:100000])
        signals.update(links.signals)
        if len(signals) >= 2:
            return 'suspect', '; '.join(sorted(signals))[:300], None
        # Sender-controlled X-Spam-* / Authentication-Results are deliberately
        # NOT trusted. No provider header has yet been verified for these accounts.
        return 'clear', 'Keine ausreichenden lokalen Spamhinweise', None
    except (MailError, ValueError, TypeError, RecursionError):
        return 'suspect', 'Nachrichteninhalt nicht prüfbar – Original prüfen', None


def assess(message, *, review_old=False, user=None):
    state, reason, rule = evaluate(message, review_old=review_old)
    changed = message.spam_state != state or message.spam_revision != message.account.spam_revision
    message.spam_state, message.spam_reason = state, reason
    message.spam_revision, message.spam_checked_at = message.account.spam_revision, timezone.now()
    message.save(update_fields=['spam_state', 'spam_reason', 'spam_revision', 'spam_checked_at'])
    if changed and state in BLOCKED:
        audit(message, reason, user)
    return rule


def blocked(message):
    return (message.spam_manual == 'spam' or message.spam_state in BLOCKED or
            message.locations.filter(folder__role='junk', folder__active=True).exists() or
            message.spam_moves.filter(state='unknown').exists())


def require_business_clear(message):
    # Re-read to keep stale browser forms from bypassing a later correction.
    message = MailMessage.objects.select_related('account').get(pk=message.pk)
    if message.spam_revision != message.account.spam_revision or message.spam_state == 'unknown':
        assess(message)
    if blocked(message):
        raise MailError('Diese Nachricht zuerst im Postfach mit „Kein Spam“ freigeben und gegebenenfalls in den Posteingang zurückholen.')


def business_source(message):
    return bool(message.customer_id or message.offers.exists() or message.tasks.exists() or
                message.replies.exists() or message.incoming_sources.exists())


def destination_for(account, role):
    folders = account.folders.filter(role=role, active=True, selectable=True)
    if folders.count() != 1:
        raise MailError('Bitte in den Postfach-Einstellungen genau einen passenden Zielordner zuordnen.')
    return folders.get()


def move_locked(client, location, destination, *, automatic=False):
    """Caller holds the lease. At most one attempt until a fresh reconciliation."""
    from .mail_imap import cache_move_target, checked_location, quoted
    if destination.account_id != location.folder.account_id or not destination.active or not destination.selectable:
        raise MailError('Bitte einen verfügbaren Ordner desselben Postfachs wählen.')
    if location.folder_id == destination.pk:
        return
    if location.message.spam_moves.filter(state='unknown').exists():
        raise MailError('Eine Verschiebung ist noch ungeklärt. Bitte zuerst das Postfach neu abrufen und den Serverstand prüfen.')
    checked_location(client, location)
    if b'MOVE' not in {item.upper() for item in client.capabilities}:
        raise MailError('Der Server unterstützt kein sicheres Verschieben (IMAP MOVE); bitte den Ordner im Webmail ändern.')
    attempt = MailSpamMove.objects.create(message=location.message, source=location.folder,
        destination=destination, uid=location.uid, uidvalidity=location.uidvalidity, automatic=automatic,
        note='Verschiebung gestartet; bei Unterbrechung zuerst Serverstand abgleichen.')
    try:
        status, _ = client.uid('MOVE', str(location.uid), quoted(destination.remote_name))
    except Exception:
        # A lost acknowledgement may mean MOVE succeeded. Never infer failure.
        raise MailError('Verschiebung unklar. Der automatische Abgleich prüft den Serverstand. Es erfolgt keine automatische Wiederholung.') from None
    if status != 'OK':
        attempt.state, attempt.note = 'failed', 'Der Server hat die Verschiebung abgelehnt.'
        attempt.save(update_fields=['state', 'note'])
        raise MailError(attempt.note)
    attempt.state, attempt.note = 'done', 'Verschiebung vom Server bestätigt.'
    attempt.save(update_fields=['state', 'note'])
    cache_move_target(client, location, destination)
    location.delete()


def reconcile(account, synced_folders):
    """Only completed, current folder snapshots may settle an interrupted MOVE."""
    for attempt in MailSpamMove.objects.filter(message__account=account, state='unknown').select_related('source', 'destination')[:BATCH]:
        if attempt.source_id not in synced_folders or attempt.destination_id not in synced_folders:
            continue
        if attempt.source.uidvalidity != attempt.uidvalidity:
            continue
        source_present = MailLocation.objects.filter(folder_id=attempt.source_id,
            uidvalidity=attempt.uidvalidity, uid=attempt.uid, message_id=attempt.message_id).exists()
        dest_present = MailLocation.objects.filter(folder_id=attempt.destination_id, message_id=attempt.message_id).exists()
        if dest_present and not source_present:
            attempt.state, attempt.note = 'done', 'Nach erneutem Abruf im Zielordner gefunden.'
        elif source_present and not dest_present:
            attempt.state, attempt.note = 'failed', 'Nach erneutem Abruf weiterhin im Ausgangsordner. Bei Bedarf bewusst erneut verschieben.'
        else:
            continue
        attempt.save(update_fields=['state', 'note'])


def process_account(client, account, token, synced_folders):
    """Runs within sync's existing lease, before any incoming-invoice intake."""
    from .mail_imap import renew
    reconcile(account, synced_folders)
    # Refresh folder-based state even when a third-party mail client moved it.
    candidates = MailMessage.objects.filter(account=account,
        locations__folder__active=True, locations__folder__role__in=['inbox', 'junk']).distinct().filter(
            Q(spam_checked_at__isnull=True) | ~Q(spam_revision=account.spam_revision) |
            Q(locations__folder__role='junk') & ~Q(spam_state='spam') |
            Q(spam_reason='Im Spamordner des Mailservers', locations__folder__role='inbox')
        ).order_by('spam_checked_at', 'pk')[:BATCH]
    for message in candidates:
        renew(account, token)
        message.account = account
        assess(message)
    if account.spam_mode != 'auto' or not account.spam_auto_since:
        return
    deadline = time.monotonic() + 10
    locations = MailLocation.objects.filter(folder__account=account, folder__active=True,
        folder__role='inbox', message__spam_state='spam', message__spam_manual='',
        message__customer__isnull=True, message__offers__isnull=True, message__tasks__isnull=True,
        message__replies__isnull=True, message__incoming_sources__isnull=True, message__content_error='',
        folder__spam_baseline_revision=account.spam_revision,
        uid__gt=F('folder__spam_baseline_uid'), uidvalidity=F('folder__spam_baseline_validity'),
        message__created_at__gte=account.spam_auto_since, message__spam_moves__isnull=True
        ).select_related('message', 'folder__account').order_by('pk')[:10]
    for location in locations:
        if time.monotonic() >= deadline:
            break
        renew(account, token)
        message = location.message
        message.account = account
        rule = assess(message)
        if not rule or rule.action != 'block' or business_source(message) or message.content_error:
            continue
        try:
            move_locked(client, location, destination_for(account, 'junk'), automatic=True)
            audit(message, 'Nach ausdrücklicher Sperrregel automatisch in Spam verschoben.')
        except MailError as exc:
            # Visible per message; never silently retry a remote operation.
            MailMessage.objects.filter(pk=message.pk).update(spam_reason=(message.spam_reason + ' · ' + str(exc))[:300])
            if message.spam_moves.filter(state='unknown').exists():
                raise


def correct(location_id, decision, user):
    from .mail_imap import foreground_lease, connection
    if decision not in ('spam', 'ham'):
        raise MailError('Ungültige Spamentscheidung.')
    initial = MailLocation.objects.select_related('folder').get(pk=location_id)
    with foreground_lease(initial.folder.account_id):
        location = MailLocation.objects.select_related('message__account', 'folder__account').filter(pk=location_id).first()
        if not location:
            raise MailError('Nachricht wurde inzwischen verschoben. Bitte die Liste aktualisieren.')
        message = location.message
        message.spam_manual = decision
        message.save(update_fields=['spam_manual'])
        # Persist the correction even if the remote operation is unavailable.
        assess(message, user=user)
        audit(message, 'Manuelle Entscheidung: ' + ('Spam' if decision == 'spam' else 'Kein Spam'), user)
        target = destination_for(message.account, 'junk' if decision == 'spam' else 'inbox')
        if location.folder_id != target.pk:
            with connection(location.folder.account) as client:
                move_locked(client, location, target)
        # No automatic source processing until the server actually shows Inbox.
        assess(message, user=user)


def spam_messages():
    return MailMessage.objects.filter(Q(spam_state__in=BLOCKED) | Q(spam_manual='spam') |
        Q(locations__folder__role='junk', locations__folder__active=True)).distinct()


def confirmed_spam_messages():
    return MailMessage.objects.filter(Q(spam_state='spam') | Q(spam_manual='spam') |
        Q(locations__folder__role='junk', locations__folder__active=True)).distinct()
