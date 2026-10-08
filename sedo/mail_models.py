"""Gemeinsame Firmenpostfächer; Entwürfe gehören ihrem jeweiligen Verfasser.

Der Nachrichteninhalt bleibt als Quelle von Angeboten/Aufgaben erhalten, auch
wenn eine IMAP-Kopie später verschoben oder vom Server entfernt wird.
"""
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class MailAccount(models.Model):
    email = models.EmailField('E-Mail-Adresse', unique=True)
    display_name = models.CharField('Absendername', max_length=160, default='SEDO')
    username = models.CharField('Postfach-Benutzername', max_length=254)
    password_encrypted = models.TextField(blank=True, editable=False)
    enabled = models.BooleanField('Automatisch abrufen', default=False)
    signature = models.TextField('Signatur', blank=True, max_length=6000)
    imap_host = models.CharField(max_length=254, default='imaps.udag.de')
    imap_port = models.PositiveIntegerField(default=993)
    smtp_host = models.CharField(max_length=254, default='smtps.udag.de')
    smtp_port = models.PositiveIntegerField(default=465)
    smtp_security = models.CharField(max_length=12, choices=[('ssl', 'SSL/TLS'), ('starttls', 'STARTTLS')], default='ssl')
    last_sync = models.DateTimeField(null=True, blank=True)
    last_error = models.CharField(max_length=400, blank=True)
    # Atomic lease protects both background sync and user mutations across workers.
    lease_token = models.CharField(max_length=36, blank=True, editable=False)
    lease_until = models.DateTimeField(null=True, editable=False)
    created_at = models.DateTimeField(default=timezone.now)
    spam_mode = models.CharField(max_length=8, default='mark', choices=[
        ('off', 'Aus'), ('mark', 'Nur markieren'), ('auto', 'Nach Regeln sortieren')])
    spam_revision = models.PositiveIntegerField(default=1)
    spam_started_at = models.DateTimeField(default=timezone.now)
    spam_auto_since = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['email']

    def __str__(self):
        return self.email


class MailFolder(models.Model):
    ROLES = [('inbox', 'Posteingang'), ('sent', 'Gesendet'), ('drafts', 'Entwürfe auf dem Server'),
             ('trash', 'Papierkorb'), ('junk', 'Spam'), ('archive', 'Archiv'), ('other', 'Eigener Ordner')]
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='folders')
    remote_name = models.CharField(max_length=1024)
    name = models.CharField(max_length=512)
    role = models.CharField(max_length=12, choices=ROLES, default='other')
    role_manual = models.BooleanField(default=False)
    selectable = models.BooleanField(default=True)
    active = models.BooleanField(default=True)
    uidvalidity = models.PositiveBigIntegerField(default=0)
    total_count = models.PositiveIntegerField(default=0)
    cached_count = models.PositiveIntegerField(default=0)
    spam_baseline_revision = models.PositiveIntegerField(default=0)
    spam_baseline_validity = models.PositiveBigIntegerField(default=0)
    spam_baseline_uid = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ['account__email', 'name']
        constraints = [models.UniqueConstraint(fields=['account', 'remote_name'], name='mail_folder_remote_unique')]

    def __str__(self):
        return self.name


class MailMessage(models.Model):
    account = models.ForeignKey(MailAccount, on_delete=models.PROTECT, related_name='messages')
    digest = models.CharField(max_length=64)
    raw = models.BinaryField(default=bytes, editable=False)
    raw_cached = models.BooleanField(default=False, editable=False)
    subject = models.CharField(max_length=998, blank=True)
    sender = models.CharField(max_length=2000, blank=True)
    sender_email = models.CharField(max_length=254, blank=True)
    recipients = models.TextField(blank=True)
    cc = models.TextField(blank=True)
    reply_to = models.TextField(blank=True)
    message_id = models.CharField(max_length=998, blank=True)
    references = models.TextField(blank=True)
    sent_at = models.DateTimeField(default=timezone.now)
    body = models.TextField(blank=True)
    # Attachments contain only metadata here; bytes stay in the original MIME.
    attachments = models.JSONField(default=list)
    size = models.PositiveBigIntegerField(default=0)
    content_error = models.CharField(max_length=300, blank=True)
    invoice_scan_done = models.BooleanField(default=False, editable=False)
    invoice_scan_error = models.CharField(max_length=240, blank=True, editable=False)
    spam_state = models.CharField(max_length=8, default='unknown', choices=[
        ('unknown', 'Noch nicht geprüft'), ('clear', 'Kein Spamhinweis'),
        ('suspect', 'Spamverdacht'), ('spam', 'Spam')])
    spam_reason = models.CharField(max_length=300, blank=True)
    spam_manual = models.CharField(max_length=4, blank=True, choices=[('spam', 'Spam'), ('ham', 'Kein Spam')])
    spam_revision = models.PositiveIntegerField(default=0)
    spam_checked_at = models.DateTimeField(null=True, blank=True)
    customer = models.ForeignKey('sedo.Customer', null=True, blank=True, on_delete=models.PROTECT, related_name='mail_messages')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-sent_at', '-pk']
        constraints = [models.UniqueConstraint(fields=['account', 'digest'], name='mail_message_digest_unique')]


class MailLocation(models.Model):
    folder = models.ForeignKey(MailFolder, on_delete=models.CASCADE, related_name='locations')
    message = models.ForeignKey(MailMessage, on_delete=models.CASCADE, related_name='locations')
    uid = models.PositiveBigIntegerField()
    uidvalidity = models.PositiveBigIntegerField()
    seen = models.BooleanField(default=False)
    flagged = models.BooleanField(default=False)
    answered = models.BooleanField(default=False)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['folder', 'uidvalidity', 'uid'], name='mail_location_uid_unique')]
        indexes = [models.Index(fields=['folder', 'seen'], name='mail_folder_seen_idx')]


class MailDraft(models.Model):
    creation_key = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    STATES = [('draft', 'Entwurf'), ('sending', 'Versand läuft'), ('sent', 'Gesendet'), ('unknown', 'Versandstatus prüfen')]
    account = models.ForeignKey(MailAccount, on_delete=models.PROTECT)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    source = models.ForeignKey(MailMessage, null=True, blank=True, on_delete=models.PROTECT, related_name='replies')
    offer = models.ForeignKey('sedo.Offer', null=True, blank=True, on_delete=models.PROTECT, related_name='mail_drafts')
    mode = models.CharField(max_length=12, default='new')
    to = models.TextField('An', blank=True, max_length=4000)
    cc = models.TextField('Kopie (Cc)', blank=True, max_length=4000)
    bcc = models.TextField('Blindkopie (Bcc)', blank=True, max_length=4000)
    subject = models.CharField('Betreff', max_length=998, blank=True)
    body = models.TextField('Nachricht', blank=True, max_length=200000)
    state = models.CharField(max_length=10, choices=STATES, default='draft')
    revision = models.PositiveIntegerField(default=0)
    message_id = models.CharField(max_length=998, blank=True)
    raw = models.BinaryField(default=bytes, editable=False)
    sent_message = models.ForeignKey(MailMessage, null=True, on_delete=models.PROTECT, related_name='+')
    sent_at = models.DateTimeField(null=True, blank=True)
    copy_saved = models.BooleanField(default=False)
    error = models.CharField(max_length=400, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']


class MailDraftAttachment(models.Model):
    draft = models.ForeignKey(MailDraft, on_delete=models.CASCADE, related_name='attachments')
    name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=150, default='application/octet-stream')
    data = models.BinaryField(editable=False)
    size = models.PositiveIntegerField()


class MailSpamRule(models.Model):
    account = models.ForeignKey(MailAccount, on_delete=models.CASCADE, related_name='spam_rules')
    kind = models.CharField(max_length=7, choices=[('address', 'Absenderadresse'), ('domain', 'Genaue Domain')])
    value = models.CharField(max_length=254)
    action = models.CharField(max_length=5, choices=[('block', 'Als Spam behandeln'), ('allow', 'Zulassen')])
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['kind', 'value']
        constraints = [models.UniqueConstraint(fields=['account', 'kind', 'value'], name='mail_spam_rule_unique')]


class MailSpamMove(models.Model):
    # Written BEFORE MOVE. An interrupted/uncertain operation is never replayed.
    message = models.ForeignKey(MailMessage, on_delete=models.CASCADE, related_name='spam_moves')
    source = models.ForeignKey(MailFolder, on_delete=models.PROTECT, related_name='+')
    destination = models.ForeignKey(MailFolder, on_delete=models.PROTECT, related_name='+')
    uid = models.PositiveBigIntegerField()
    uidvalidity = models.PositiveBigIntegerField()
    state = models.CharField(max_length=8, default='unknown', choices=[
        ('unknown', 'Serverstand prüfen'), ('done', 'Verschoben'), ('failed', 'Nicht verschoben')])
    automatic = models.BooleanField(default=False)
    note = models.CharField(max_length=300, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-pk']
