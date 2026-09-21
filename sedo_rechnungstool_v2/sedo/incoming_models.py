"""Originale von Eingangsbelegen bleiben mit der Datenbank sicherbar."""
from django.conf import settings
from django.db import models
from django.utils import timezone


class IncomingInvoice(models.Model):
    STATES = [('review', 'Zu prüfen'), ('approved', 'Freigegeben'), ('ignored', 'Kein Rechnungsbeleg')]
    digest = models.CharField(max_length=64, unique=True, editable=False)
    original = models.BinaryField(editable=False)
    filename = models.CharField(max_length=240)
    media_type = models.CharField(max_length=80)
    size = models.PositiveIntegerField()
    reason = models.CharField(max_length=240)
    state = models.CharField(max_length=12, choices=STATES, default='review')
    supplier = models.CharField('Lieferant', max_length=240, blank=True)
    number = models.CharField('Rechnungsnummer', max_length=120, blank=True)
    document_date = models.DateField('Belegdatum', null=True, blank=True)
    gross = models.DecimalField('Bruttobetrag', max_digits=14, decimal_places=2, null=True, blank=True)
    currency = models.CharField('Währung', max_length=3, default='EUR')
    note = models.TextField('Interne Notiz', max_length=3000, blank=True)
    revision = models.PositiveIntegerField(default=0)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True)
    printed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at', '-pk']
        indexes = [models.Index(fields=['state', 'document_date'], name='incoming_state_date_idx')]


class IncomingInvoiceSource(models.Model):
    invoice = models.ForeignKey(IncomingInvoice, on_delete=models.PROTECT, related_name='sources')
    message = models.ForeignKey('sedo.MailMessage', on_delete=models.PROTECT, related_name='incoming_sources')
    attachment_index = models.PositiveIntegerField()

    class Meta:
        constraints = [models.UniqueConstraint(fields=['message', 'attachment_index'], name='incoming_mail_part_unique')]


class IncomingInvoicePackage(models.Model):
    month = models.CharField(max_length=7)
    created_at = models.DateTimeField(default=timezone.now)
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    manifest = models.JSONField(default=dict)
    data = models.BinaryField(editable=False)
    digest = models.CharField(max_length=64, editable=False)
    # One prepared message per package; SMTP status remains owned by MailDraft.
    draft = models.OneToOneField('sedo.MailDraft', null=True, on_delete=models.SET_NULL, related_name='incoming_package')

    class Meta:
        ordering = ['-created_at', '-pk']
