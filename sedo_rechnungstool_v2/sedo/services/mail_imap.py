"""IMAP operations address UID + UIDVALIDITY, never mutable sequence numbers."""
import base64
import imaplib
import re
import ssl
import time
import uuid
import certifi
from contextlib import contextmanager
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from sedo.models import MailAccount, MailFolder, MailLocation
from .mail_mime import MAX_MESSAGE_BYTES, store_message
from .mail_secrets import MailError, decrypt_password
from .mail_storage import cache_exists

TIMEOUT = 20
SYNC_BATCH = 100
SYNC_FOLDER_BATCH = 25
SYNC_SECONDS = 12


class MailBusy(MailError):
    pass


def tls_context():
    context = ssl.create_default_context()
    # A frozen Mac app cannot rely on the build machine's OpenSSL CA path.
    # Add the bundled public CA store while preserving platform trust and validation.
    context.load_verify_locations(cafile=certifi.where())
    return context


@contextmanager
def account_lease(account_id):
    token = str(uuid.uuid4())
    now = timezone.now()
    acquired = MailAccount.objects.filter(pk=account_id).filter(
        Q(lease_until__isnull=True) | Q(lease_until__lt=now)
    ).update(lease_token=token, lease_until=now + timedelta(minutes=3))
    if not acquired:
        raise MailBusy('Dieses Postfach wird gerade abgeglichen. Bitte kurz warten und erneut versuchen.')
    try:
        yield token
    finally:
        MailAccount.objects.filter(pk=account_id, lease_token=token).update(lease_token='', lease_until=None)


def renew(account, token):
    if not MailAccount.objects.filter(pk=account.pk, lease_token=token).update(lease_until=timezone.now() + timedelta(minutes=3)):
        raise MailBusy('Der Postfachabgleich wurde von einem anderen Vorgang übernommen.')


def checked(response):
    status, data = response
    if status != 'OK':
        raise MailError('Der Mailserver hat den Vorgang abgelehnt. Bitte Verbindung und Ordner prüfen.')
    return data


@contextmanager
def connection(account):
    client = None
    try:
        password = decrypt_password(account)
        client = imaplib.IMAP4_SSL(account.imap_host, account.imap_port,
                                  ssl_context=tls_context(), timeout=TIMEOUT)
        checked(client.login(account.username, password))
        # Servers can advertise different extensions after authentication.
        capabilities = checked(client.capability())
        client.capabilities = tuple(b' '.join(item for item in capabilities if isinstance(item, bytes)).upper().split())
        yield client
    except ssl.SSLCertVerificationError as exc:
        raise MailError('Das TLS-Zertifikat des Mailservers ist ungültig. Die Verbindung wurde abgebrochen.') from exc
    except (imaplib.IMAP4.error, OSError, EOFError) as exc:
        # Never forward server-provided strings: some servers echo LOGIN arguments.
        raise MailError('Die Verbindung zum Postfach ist fehlgeschlagen. Bitte Benutzername, Passwort und Server prüfen.') from exc
    finally:
        if client is not None:
            try:
                client.logout()  # CLOSE would implicitly expunge unrelated messages.
            except Exception:
                pass


def decode_folder(value):
    def replace(match):
        raw = match.group(1)
        if not raw:
            return '&'
        try:
            raw = raw.replace(',', '/')
            return base64.b64decode(raw + '=' * (-len(raw) % 4), validate=True).decode('utf-16-be')
        except (ValueError, UnicodeError):
            return match.group(0)
    return re.sub(r'&([^-]*)-', replace, value)


def encode_folder(value):
    result, pending = [], []
    def flush():
        if pending:
            result.append('&' + base64.b64encode(''.join(pending).encode('utf-16-be')).decode().rstrip('=').replace('/', ',') + '-')
            pending.clear()
    for char in value:
        if ' ' <= char <= '~':
            flush()
            result.append('&-' if char == '&' else char)
        else:
            pending.append(char)
    flush()
    return ''.join(result)


def quoted(value):
    if any(char in value for char in '\r\n\x00'):
        raise MailError('Der Ordnername enthält ungültige Zeichen.')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _folder_entry(line):
    literal = None
    if isinstance(line, tuple):
        line, literal = line
    match = re.match(rb'^\(([^)]*)\)\s+(?:NIL|"(?:\\.|[^"\\])*")\s+(.+)$', line or b'')
    if not match:
        raise MailError('Die Ordnerliste konnte nicht gelesen werden.')
    flags, name = match.groups()
    if literal is not None:
        name = literal
    elif name.startswith(b'"') and name.endswith(b'"'):
        name = re.sub(rb'\\(.)', rb'\1', name[1:-1])
    try:
        remote = name.decode('ascii')
    except UnicodeError as exc:
        raise MailError('Der Server lieferte einen nicht unterstützten Ordnernamen.') from exc
    quoted(remote)
    return remote, flags.lower().split()


def discover(client, account):
    entries = checked(client.list())
    parsed = [_folder_entry(line) for line in entries if line not in (None, b'', b')')]
    names = []
    for remote, flags in parsed:
        name = decode_folder(remote)
        role = 'other'
        for key, flag in [('inbox', b'\\inbox'), ('sent', b'\\sent'), ('trash', b'\\trash'),
                          ('drafts', b'\\drafts'), ('junk', b'\\junk'), ('archive', b'\\archive')]:
            if flag in flags:
                role = key
                break
        if remote.upper() == 'INBOX':
            role = 'inbox'
        elif role == 'other':
            leaf = re.split(r'[/\.]', name)[-1].lower()
            role = {'sent': 'sent', 'sent items': 'sent', 'gesendet': 'sent', 'trash': 'trash',
                    'deleted items': 'trash', 'papierkorb': 'trash', 'drafts': 'drafts', 'entwürfe': 'drafts',
                    'junk': 'junk', 'spam': 'junk', 'archive': 'archive', 'archiv': 'archive'}.get(leaf, 'other')
        folder, _ = MailFolder.objects.get_or_create(account=account, remote_name=remote, defaults={'name': name})
        folder.name, folder.selectable, folder.active = name, b'\\noselect' not in flags, True
        if not folder.role_manual:
            folder.role = role
        folder.save(update_fields=['name', 'selectable', 'active', 'role'])
        names.append(remote)
    MailFolder.objects.filter(account=account).exclude(remote_name__in=names).update(active=False)
    return list(MailFolder.objects.filter(account=account, active=True, selectable=True).order_by('name'))


def select(client, folder, *, readonly=True):
    checked(client.select(quoted(folder.remote_name), readonly=readonly))
    response = client.response('UIDVALIDITY')[1]
    try:
        value = int(response[0])
        if value <= 0:
            raise ValueError()
        return value
    except (ValueError, TypeError, IndexError) as exc:
        raise MailError('Der Server hat keine gültige Ordnerkennung geliefert. Es wurden keine Nachrichten verändert.') from exc


def _metadata(data):
    result = {}
    for line in data:
        if not isinstance(line, bytes):
            continue
        uid = re.search(rb'\bUID (\d+)', line, re.I)
        size = re.search(rb'\bRFC822.SIZE (\d+)', line, re.I)
        if uid:
            flags = [flag.lower() for flag in imaplib.ParseFlags(line)]
            if b'\\deleted' not in flags:
                result[int(uid[1])] = (int(size[1]) if size else 0, flags)
    return result


def _apply_flags(location, flags):
    location.seen = b'\\seen' in flags
    location.flagged = b'\\flagged' in flags
    location.answered = b'\\answered' in flags


def sync_folder(client, folder, budget, deadline, heartbeat=None):
    validity = select(client, folder)
    # An empty selected mailbox may answer FETCH 1:* with NO on some servers.
    uids_raw = checked(client.uid('SEARCH', None, 'UNDELETED'))
    live_uids = {int(value) for value in (uids_raw[0] or b'').split()}
    metadata = {}
    sorted_uids = sorted(live_uids)
    for offset in range(0, len(sorted_uids), 500):
        if heartbeat:
            heartbeat()
        ids = ','.join(map(str, sorted_uids[offset:offset + 500]))
        metadata.update(_metadata(checked(client.uid('FETCH', ids, '(UID FLAGS RFC822.SIZE)'))))
    # UIDs absent from FETCH may have been concurrently expunged; never use a sequence ID.
    existing = {location.uid: location for location in folder.locations.filter(uidvalidity=validity).select_related('message').only(
        'id', 'folder_id', 'uid', 'uidvalidity', 'seen', 'flagged', 'answered', 'message_id',
        'message__id', 'message__account_id', 'message__digest', 'message__raw_cached')}
    updates = []
    for uid in existing.keys() & metadata.keys():
        location = existing[uid]
        before = (location.seen, location.flagged, location.answered)
        _apply_flags(location, metadata[uid][1])
        if before != (location.seen, location.flagged, location.answered):
            updates.append(location)
    with transaction.atomic():
        folder.locations.exclude(uidvalidity=validity).delete()
        gone = [location.pk for uid, location in existing.items() if uid not in live_uids]
        for offset in range(0, len(gone), 500):
            folder.locations.filter(pk__in=gone[offset:offset + 500]).delete()
        if updates:
            MailLocation.objects.bulk_update(updates, ['seen', 'flagged', 'answered'], batch_size=150)
        folder.uidvalidity = validity
        folder.total_count = len(live_uids)
        folder.save(update_fields=['uidvalidity', 'total_count'])
    loaded = 0
    loaded_uids = set()
    missing_cache = {uid for uid, location in existing.items() if uid in metadata and not cache_exists(location.message)}
    for uid in sorted((metadata.keys() - existing.keys()) | missing_cache, reverse=True):
        if loaded >= budget or time.monotonic() >= deadline:
            break
        if heartbeat:
            heartbeat()
        size, flags = metadata[uid]
        too_large = size > MAX_MESSAGE_BYTES
        spec = '(UID BODY.PEEK[HEADER]<0.262144>)' if too_large else f'(UID BODY.PEEK[]<0.{MAX_MESSAGE_BYTES + 1}>)'
        data = checked(client.uid('FETCH', str(uid), spec))
        raw = None
        for item in data:
            if isinstance(item, tuple) and re.search(rb'\bUID ' + str(uid).encode() + rb'\b', item[0]):
                raw = item[1]
                break
        if raw is None:
            continue  # concurrent deletion, not an empty message
        if len(raw) > MAX_MESSAGE_BYTES:
            raw = raw[:262144].split(b'\r\n\r\n', 1)[0] + b'\r\n\r\n'
            too_large = True
        error = 'Diese Nachricht ist größer als 50 MB. Inhalt und Anhänge bitte im United-Domains-Webmail öffnen.' if too_large else ''
        message = store_message(folder.account, raw, size=size, content_error=error, cache_only=True)
        location = MailLocation(folder=folder, message=message, uid=uid, uidvalidity=validity)
        _apply_flags(location, flags)
        MailLocation.objects.update_or_create(folder=folder, uid=uid, uidvalidity=validity,
            defaults={'message': message, 'seen': location.seen, 'flagged': location.flagged, 'answered': location.answered})
        loaded += 1
        loaded_uids.add(uid)
    folder.cached_count = folder.locations.count() - len(missing_cache - loaded_uids)
    folder.save(update_fields=['cached_count'])
    return loaded


def sync_account(account_id):
    with account_lease(account_id) as token:
        account = MailAccount.objects.get(pk=account_id)
        try:
            with connection(account) as client:
                folders = discover(client, account)
                # Every folder gets a fair share: a large Inbox cannot starve Sent.
                folders.sort(key=lambda item: (item.role != 'inbox', item.name))
                remaining = SYNC_BATCH
                deadline = time.monotonic() + SYNC_SECONDS
                for folder in folders:
                    renew(account, token)
                    remaining -= sync_folder(client, folder, min(SYNC_FOLDER_BATCH, max(0, remaining)), deadline,
                                             heartbeat=lambda: renew(account, token))
            MailAccount.objects.filter(pk=account_id).update(last_sync=timezone.now(), last_error='')
            from .incoming_invoices import collect_account
            collect_account(account_id)
        except MailError as exc:
            MailAccount.objects.filter(pk=account_id).update(last_error=str(exc)[:400])
            raise


def checked_location(client, location):
    if not location.folder.active or select(client, location.folder, readonly=False) != location.uidvalidity:
        raise MailError('Der Ordner hat sich auf dem Server geändert. Bitte zuerst neu abrufen.')
    result = checked(client.uid('SEARCH', None, 'UID', str(location.uid)))
    if str(location.uid).encode() not in (result[0] or b'').split():
        raise MailError('Diese Nachricht wurde auf dem Server bereits verschoben oder gelöscht. Bitte neu abrufen.')


def set_flag(location, flag, enabled):
    if flag not in ('seen', 'flagged'):
        raise MailError('Unbekannte Nachrichtenmarkierung.')
    with account_lease(location.folder.account_id):
        with connection(location.folder.account) as client:
            checked_location(client, location)
            checked(client.uid('STORE', str(location.uid), '+FLAGS.SILENT' if enabled else '-FLAGS.SILENT',
                               '(\\Seen)' if flag == 'seen' else '(\\Flagged)'))
            setattr(location, flag, enabled)
            location.save(update_fields=[flag])


def move_message(location, destination):
    if destination.account_id != location.folder.account_id or not destination.active or not destination.selectable:
        raise MailError('Bitte einen verfügbaren Ordner desselben Postfachs wählen.')
    if location.folder_id == destination.pk:
        return
    with account_lease(location.folder.account_id):
        with connection(location.folder.account) as client:
            checked_location(client, location)
            capabilities = {item.upper() for item in client.capabilities}
            if b'MOVE' not in capabilities:
                # COPY + DELETE is not atomic; retries after lost acknowledgements duplicate mail.
                raise MailError('Dieser Mailserver unterstützt kein sicheres Verschieben (IMAP MOVE). Bitte vorerst das Webmail verwenden.')
            checked(client.uid('MOVE', str(location.uid), quoted(destination.remote_name)))
            location.delete()


def delete_permanently(location):
    if location.folder.role != 'trash':
        raise MailError('Endgültig löschen ist nur im Papierkorb möglich.')
    with account_lease(location.folder.account_id):
        with connection(location.folder.account) as client:
            checked_location(client, location)
            if b'UIDPLUS' not in {item.upper() for item in client.capabilities}:
                raise MailError('Der Server unterstützt kein gezieltes endgültiges Löschen. Bitte das Webmail verwenden.')
            checked(client.uid('STORE', str(location.uid), '+FLAGS.SILENT', '(\\Deleted)'))
            checked(client.uid('EXPUNGE', str(location.uid)))
            location.delete()


def create_folder(account, name):
    if not name.strip() or len(name) > 150 or any(ord(char) < 32 for char in name):
        raise MailError('Bitte einen gültigen Ordnernamen mit höchstens 150 Zeichen eingeben.')
    with account_lease(account.pk):
        with connection(account) as client:
            checked(client.create(quoted(encode_folder(name.strip()))))
            discover(client, account)


def append_sent(account, raw):
    # Caller holds the account lease. Search stable Message-ID before any retry.
    from .mail_mime import parse, header
    folders = MailFolder.objects.filter(account=account, active=True, selectable=True, role='sent')
    if folders.count() != 1:
        raise MailError('Bitte in den Postfach-Einstellungen genau einen Ordner „Gesendet“ zuordnen.')
    folder = folders.get()
    with connection(account) as client:
        select(client, folder)
        mid = header(parse(raw), 'Message-ID', 998)
        found = checked(client.uid('SEARCH', None, 'HEADER', 'Message-ID', quoted(mid)))
        if not (found[0] or b'').strip():
            checked(client.append(quoted(folder.remote_name), '(\\Seen)', None, raw))
