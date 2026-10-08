"""Explicit SMTP submission with an immutable attempt and no blind retries."""
import re
from contextlib import suppress
import smtplib
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, format_datetime, getaddresses, make_msgid

from django.db.models import F
from django.utils import timezone

from sedo.models import MailAccount, MailDraft
from .mail_imap import account_lease, append_sent, tls_context
from .mail_mime import MAX_MESSAGE_BYTES, addresses, attachment_bytes, safe_name, store_message
from .mail_secrets import MailError, decrypt_password

MAX_ATTACHMENTS_BYTES = 25 * 1024 * 1024


def smtp_connection(account):
    password = decrypt_password(account)
    client = None
    try:
        context = tls_context()
        if account.smtp_security == 'starttls':
            client = smtplib.SMTP(account.smtp_host, account.smtp_port, timeout=20)
            client.ehlo()
            client.starttls(context=context)
            client.ehlo()
        else:
            client = smtplib.SMTP_SSL(account.smtp_host, account.smtp_port, timeout=20, context=context)
        client.login(account.username, password)
        return client
    except Exception:
        if client is not None:
            client.close()
        raise


def test_account(account):
    from .mail_imap import connection, discover
    with account_lease(account.pk):
        with connection(account) as client:
            discover(client, account)
        smtp = None
        try:
            smtp = smtp_connection(account)
        except (OSError, smtplib.SMTPException) as exc:
            raise MailError('IMAP ist erreichbar, SMTP noch nicht. Bitte Versandserver und Zugangsdaten prüfen.') from exc
        finally:
            if smtp:
                smtp.close()


def reply_initial(source, mode, account):
    own = {address.lower() for address in MailAccount.objects.values_list('email', flat=True)}
    sender = source.reply_to or source.sender
    if source.sender_email.lower() in own:
        sender = source.recipients
    def others(values):
        return ', '.join(formataddr((name, addr)) for name, addr in getaddresses(values)
                         if addr and addr.lower() not in own)
    prefix = 'Fwd: ' if mode == 'forward' else 'Re: '
    subject = source.subject
    if not re.match(r'^(re|aw|fwd|wg):', subject, re.I) or mode == 'forward':
        subject = prefix + subject
    to = '' if mode == 'forward' else others([sender])
    cc = others([source.recipients, source.cc]) if mode == 'reply_all' else ''
    # Reply-all recipients are deduplicated again in the SMTP envelope.
    intro = f'Am {timezone.localtime(source.sent_at):%d.%m.%Y %H:%M} schrieb {source.sender}:\n'
    quote = '\n'.join('> ' + line for line in source.body[:150000].splitlines())
    if mode == 'forward':
        intro = f'Weitergeleitete Nachricht\nVon: {source.sender}\nAn: {source.recipients}\nBetreff: {source.subject}\n\n'
        quote = source.body[:150000]
    return {'to': to, 'cc': cc, 'subject': subject[:998],
            'body': '\n\n' + account.signature + '\n\n' + intro + quote}


def forward_attachments(draft, source):
    total = sum(item['size'] for item in source.attachments)
    if total > MAX_ATTACHMENTS_BYTES:
        raise MailError('Die weitergeleiteten Anhänge sind zusammen größer als 25 MB.')
    for item in source.attachments:
        name, data = attachment_bytes(source, item['index'])
        draft.attachments.create(name=name, data=data, size=len(data), content_type=item['content_type'])


def prepare(draft):
    from .incoming_invoices import check_package_attachment
    check_package_attachment(draft)
    recipients = list({address.lower(): address for address in addresses(draft.to) + addresses(draft.cc) + addresses(draft.bcc)}.values())
    if not recipients:
        raise MailError('Bitte mindestens einen Empfänger angeben.')
    if len(recipients) > 100:
        raise MailError('Eine Nachricht darf höchstens 100 Empfänger einschließlich Cc und Bcc haben.')
    if not draft.subject.strip():
        raise MailError('Bitte einen Betreff angeben.')
    if any(c in draft.subject for c in '\r\n\x00'):
        raise MailError('Der Betreff enthält ungültige Zeichen.')
    message = EmailMessage(policy=policy.SMTP)
    message['From'] = formataddr((draft.account.display_name, draft.account.email))
    for header, value in [('To', draft.to), ('Cc', draft.cc)]:
        if value.strip():
            message[header] = value.replace(';', ',')
    # Bcc only goes into the SMTP envelope, never the MIME or Sent copy.
    message['Subject'] = draft.subject
    message['Date'] = format_datetime(timezone.now())
    message['Message-ID'] = draft.message_id or make_msgid(domain=draft.account.email.rsplit('@', 1)[-1])
    if draft.source_id and draft.mode in ('reply', 'reply_all'):
        ids = re.findall(r'<[^\s<>]+@[^\s<>]+>', draft.source.references + ' ' + draft.source.message_id)[-20:]
        if ids:
            message['In-Reply-To'] = ids[-1]
            message['References'] = ' '.join(ids)
    message.set_content(draft.body)
    total = 0
    for attachment in draft.attachments.all():
        total += attachment.size
        if total > MAX_ATTACHMENTS_BYTES:
            raise MailError('Anhänge dürfen zusammen höchstens 25 MB groß sein.')
        # User-supplied MIME types cannot inject headers or cause inline execution.
        content_type = attachment.content_type.split('/')
        if len(content_type) != 2 or not all(re.fullmatch(r'[a-zA-Z0-9.+-]+', part) for part in content_type):
            content_type = ['application', 'octet-stream']
        message.add_attachment(bytes(attachment.data), maintype=content_type[0], subtype=content_type[1], filename=safe_name(attachment.name))
    raw = message.as_bytes()
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MailError('Die vollständige Nachricht ist größer als 50 MB.')
    return raw, recipients, str(message['Message-ID'])


def save_sent_copy(draft):
    if draft.state != 'sent' or not draft.raw:
        raise MailError('Nur bereits bestätigte Sendungen können im Ordner „Gesendet“ gespeichert werden.')
    if draft.copy_saved:
        return
    if not draft.sent_message_id:
        message = store_message(draft.account, bytes(draft.raw))
        MailDraft.objects.filter(pk=draft.pk, state='sent').update(sent_message=message)
    append_sent(draft.account, bytes(draft.raw))
    MailDraft.objects.filter(pk=draft.pk, state='sent').update(copy_saved=True, error='')


def send_draft(draft_id, author, revision):
    draft = MailDraft.objects.select_related('account', 'source').get(pk=draft_id, author=author)
    with account_lease(draft.account_id):
        raw, recipients, message_id = prepare(draft)
        claimed = MailDraft.objects.filter(pk=draft.pk, author=author, state='draft', revision=revision).update(
            state='sending', raw=raw, message_id=message_id, error='', updated_at=timezone.now(), revision=F('revision') + 1)
        if not claimed:
            raise MailError('Dieser Entwurf wurde bereits versendet oder inzwischen geändert. Bitte den aktuellen Stand öffnen.')
        client = None
        data_started = False
        accepted = False
        try:
            client = smtp_connection(draft.account)
            code, _ = client.mail(draft.account.email)
            if code != 250:
                raise MailError('Der Mailserver hat die Absenderadresse abgelehnt. Es wurde nichts versendet.')
            for recipient in recipients:
                code, _ = client.rcpt(recipient)
                if code not in (250, 251, 252):
                    client.rset()
                    raise MailError('Mindestens eine Empfängeradresse wurde abgelehnt. Es wurde an niemanden versendet; bitte alle Adressen prüfen.')
            data_started = True
            code, _ = client.data(raw)
            if code != 250:
                data_started = False  # explicit negative reply, server did not accept the message
                raise MailError('Der Mailserver hat die Nachricht abgelehnt. Bitte Inhalt und Anhänge prüfen.')
            accepted = True
            sent_message = store_message(draft.account, raw)
            MailDraft.objects.filter(pk=draft.pk).update(state='sent', sent_at=timezone.now(), sent_message=sent_message, error='')
        except Exception as exc:
            if accepted:
                # SMTP acceptance is final even if storing a local copy failed.
                MailDraft.objects.filter(pk=draft.pk).update(state='sent', sent_at=timezone.now(), error='Versendet; die lokale Kopie muss erneut gespeichert werden.')
                raise MailError('Die E-Mail wurde versendet. Bitte die Versandkopie erneut speichern; nicht erneut senden.') from exc
            if isinstance(exc, smtplib.SMTPDataError):
                data_started = False
            state = 'unknown' if data_started else 'draft'
            error = ('Die Verbindung brach während der Übertragung ab. Die Nachricht kann bereits versendet sein. '
                     'Bitte im Webmail oder beim Empfänger prüfen; SEDO sendet nicht automatisch erneut.') if data_started else (
                     str(exc) if isinstance(exc, MailError) else 'Der Versand konnte nicht gestartet werden. Bitte Verbindung und Zugangsdaten prüfen.')
            MailDraft.objects.filter(pk=draft.pk).update(state=state, error=error)
            raise MailError(error) from exc
        finally:
            if client is not None:
                with suppress(OSError, smtplib.SMTPException):
                    client.close()
        draft.refresh_from_db()
        try:
            save_sent_copy(draft)
        except MailError:
            MailDraft.objects.filter(pk=draft.pk).update(error='Versendet. Die Serverkopie fehlt noch; „Versandkopie speichern“ versucht nur die Ablage erneut.')
        return MailDraft.objects.get(pk=draft.pk)
