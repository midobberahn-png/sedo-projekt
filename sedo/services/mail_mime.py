"""Bounded MIME parsing and a deliberately passive HTML representation."""
import hashlib
import re
from datetime import timezone as dt_timezone
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

import bleach
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.utils import timezone

from sedo.models import MailMessage
from .mail_secrets import MailError
from .mail_storage import original_bytes, write_cached

MAX_MESSAGE_BYTES = 50 * 1024 * 1024
MAX_BODY_CHARS = 2_000_000
MAX_PARTS = 300


def parse(raw):
    if len(raw) > MAX_MESSAGE_BYTES:
        raise MailError('Diese Nachricht ist größer als 50 MB. Bitte im Webmail öffnen.')
    return BytesParser(policy=policy.default).parsebytes(bytes(raw))


def header(msg, name, limit=4000):
    return re.sub(r'[\r\n\x00]', ' ', str(msg.get(name, '')))[:limit]


def addresses(value):
    """No silent discarding of malformed recipients or header injection."""
    if any(c in value for c in '\r\n\x00'):
        raise MailError('Bitte gültige E-Mail-Adressen ohne Zeilenumbruch eingeben.')
    result = []
    if value.strip():
        for name, address in getaddresses([value.replace(';', ',')]):
            try:
                validate_email(address)
                address.encode('ascii')  # SMTPUTF8 is intentionally not negotiated.
            except (ValidationError, UnicodeError):
                raise MailError('Eine Empfängeradresse ist ungültig. Adressen bitte mit Komma trennen.')
            if address.lower() not in [x.lower() for x in result]:
                result.append(address)
        if not result or len(result) > 100:
            raise MailError('Bitte zwischen einer und 100 Empfängeradressen angeben.')
    return result


def safe_name(value):
    value = re.split(r'[/\\]', str(value or 'Anhang'))[-1]
    value = re.sub(r'[\x00-\x1f\x7f]', '', value).strip('. ')
    return value[:240] or 'Anhang'


def parts(msg):
    """Iterative traversal, stopping at attached messages and multipart attachments."""
    stack, count = [msg], 0
    while stack:
        part = stack.pop()
        count += 1
        if count > MAX_PARTS:
            raise MailError('Die Nachricht enthält zu viele MIME-Teile. Bitte das Original im Webmail prüfen.')
        attachment = part.get_content_disposition() == 'attachment' or bool(part.get_filename()) or part.get_content_type() == 'message/rfc822'
        if part.is_multipart() and not attachment:
            stack.extend(reversed(part.get_payload()))
        else:
            yield part, attachment


def decoded(part):
    data = part.get_payload(decode=True)
    if data is None:
        data = part.as_bytes(policy=policy.SMTP) if part.is_multipart() else str(part.get_payload()).encode('utf-8')
    return data


def text_content(part):
    try:
        return decoded(part).decode(part.get_content_charset() or 'utf-8', errors='replace')[:MAX_BODY_CHARS]
    except LookupError:
        return decoded(part).decode('utf-8', errors='replace')[:MAX_BODY_CHARS]


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []

    def handle_data(self, data):
        self.text.append(data)

    def handle_starttag(self, tag, attrs):
        if tag in ('p', 'div', 'br', 'li', 'tr'):
            self.text.append('\n')


def safe_html(value):
    # No images, styles, forms, SVG, remote resources, scripts or auto-navigation.
    cleaner = bleach.Cleaner(tags=['p', 'br', 'div', 'span', 'b', 'strong', 'i', 'em', 'u',
        'blockquote', 'pre', 'code', 'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tr', 'td',
        'th', 'hr', 'h1', 'h2', 'h3', 'h4', 'a'], attributes={'a': ['href', 'title']},
        protocols=['https', 'http', 'mailto'], strip=True, strip_comments=True)
    cleaned = cleaner.clean(value[:MAX_BODY_CHARS])
    # Open user-clicked links outside the sandbox; never in the app's top frame.
    return re.sub(r'<a\b', '<a target="_blank" rel="noopener noreferrer"', cleaned)


def body_and_attachments(msg):
    plain, html, attachments = [], [], []
    for index, (part, attached) in enumerate(parts(msg)):
        if attached or part.get_content_maintype() not in ('text',):
            data = decoded(part)
            attachments.append({'index': index, 'name': safe_name(part.get_filename() or ('Nachricht.eml' if part.get_content_type() == 'message/rfc822' else 'Anhang')),
                                'size': len(data), 'content_type': part.get_content_type()})
        elif part.get_content_type() == 'text/plain':
            plain.append(text_content(part))
        elif part.get_content_type() == 'text/html':
            html.append(text_content(part))
    html_text = '\n'.join(html)[:MAX_BODY_CHARS]
    body = '\n'.join(plain)[:MAX_BODY_CHARS]
    if not body and html_text:
        parser = _Text()
        parser.feed(safe_html(html_text))
        body = ''.join(parser.text)[:MAX_BODY_CHARS]
    return body, html_text, attachments


def attachment_bytes(message, index):
    if message.content_error:
        raise MailError('Diese Nachricht ist nicht vollständig geladen.')
    msg = parse(original_bytes(message))
    metadata = next((item for item in message.attachments if item['index'] == index), None)
    if metadata is None:
        raise MailError('Dieser Anhang ist nicht vorhanden.')
    for part_index, (part, _) in enumerate(parts(msg)):
        if part_index == index:
            return metadata['name'], decoded(part)
    raise MailError('Dieser Anhang ist nicht vorhanden.')


def store_message(account, raw, *, size=None, content_error='', cache_only=False):
    digest = hashlib.sha256(raw).hexdigest()
    existing = MailMessage.objects.filter(account=account, digest=digest).only('pk', 'raw_cached').first()
    if cache_only and (not existing or existing.raw_cached):
        write_cached(account.pk, digest, raw)
    if existing:
        if existing.raw_cached and not cache_only:
            MailMessage.objects.filter(pk=existing.pk).update(raw=raw, raw_cached=False)
            existing.raw, existing.raw_cached = raw, False
        return existing
    try:
        msg = parse(raw)
        subject = header(msg, 'Subject', 998)
        body, _, attachments = body_and_attachments(msg)
        sender = header(msg, 'From', 2000)
        sender_pairs = getaddresses([sender])
        sender_email = sender_pairs[0][1][:254] if sender_pairs else ''
        try:
            date = parsedate_to_datetime(header(msg, 'Date'))
            if not date.tzinfo:
                date = date.replace(tzinfo=dt_timezone.utc)
            if not 1900 <= date.year <= 9998:
                date = timezone.now()
        except (ValueError, TypeError, OverflowError):
            date = timezone.now()
        defaults = dict(raw=raw, subject=subject, sender=sender, sender_email=sender_email,
                        recipients=header(msg, 'To'), cc=header(msg, 'Cc'), reply_to=header(msg, 'Reply-To'),
                        message_id=header(msg, 'Message-ID', 998), references=header(msg, 'References', 8000),
                        sent_at=date, body=body, attachments=attachments, size=size or len(raw), content_error=content_error)
    except (MailError, ValueError, TypeError, RecursionError):
        defaults = dict(raw=raw, subject='Nachricht konnte nicht vollständig gelesen werden', size=size or len(raw),
                        content_error='Das Nachrichtenformat konnte nicht gelesen werden. Das Original bleibt abrufbar.')
    if cache_only:
        defaults.update(raw=b'', raw_cached=True)
    return MailMessage.objects.get_or_create(account=account, digest=digest, defaults=defaults)[0]
