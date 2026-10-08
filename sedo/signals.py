from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import pre_delete
from django.dispatch import receiver

from .audit import write_audit
from .models import (
    AuditLog, CashbookEntry, DeleteProtectedError, DunningDocument, Invoice, Offer, Payment,
)


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    write_audit(request, 'login', object_type='User', object_id=str(user.pk), object_repr=user.get_username(), message='Benutzer angemeldet')


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    if user:
        write_audit(request, 'logout', object_type='User', object_id=str(user.pk), object_repr=user.get_username(), message='Benutzer abgemeldet')


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request, **kwargs):
    username = credentials.get('username', '') if credentials else ''
    write_audit(request, 'security', object_type='Login', object_repr=username, message='Fehlgeschlagener Loginversuch')


# --- Löschsperre für aufbewahrungspflichtige Belege (Review-Fund F-02) -------

# Diese Modelle dürfen die Datenbank nie hart verlassen. Die App selbst hält
# sich daran (invoice_delete, offer_delete und payment_delete blenden nur aus),
# aber der Django-Admin, `manage.py shell` und jedes QuerySet.delete() gingen
# bisher daran vorbei – samt CASCADE auf Positionen und Mahnungen.
GESCHUETZTE_BELEG_MODELLE = (
    Invoice,        # Rechnung, CASCADE auf InvoiceItem und DunningDocument
    Offer,          # Angebot – Geschäftsbrief, CASCADE auf OfferItem
    Payment,        # Zahlungsbeleg
    DunningDocument,  # Mahnung/Zahlungserinnerung – ausgegangener Geschäftsbrief
    CashbookEntry,  # Kassenbuchung
    AuditLog,       # das Protokoll selbst
)


def _block_hard_delete(sender, instance, **kwargs):
    """Verhindert das harte Löschen eines aufbewahrungspflichtigen Belegs.

    Bewusst je Modell verbunden (``sender=…``), nicht global: die Migration
    0012 räumt über ``apps.get_model`` doppelte Mahndokumente auf (historische
    Modellklasse, andere Identität – bleibt also möglich), und die Views
    löschen legitim ``ServiceCatalogItem``, ``CustomerObject``,
    ``AareonMapping`` und ``RecurringInvoiceTemplate``.

    ``InvoiceItem`` und ``OfferItem`` stehen absichtlich nicht in der Liste:
    Sie hängen über CASCADE am geschützten Elternbeleg und sind damit gedeckt.

    Zum bewussten Aussetzen – etwa für eine spätere Bereinigung nach Ablauf
    der Aufbewahrungsfrist – lässt sich der Empfänger über die ``dispatch_uid``
    ``sedo_block_hard_delete`` gezielt trennen.
    """
    bezeichnung = getattr(instance, 'number', None) or f'#{instance.pk}'
    raise DeleteProtectedError(
        f'{sender._meta.verbose_name} {bezeichnung} ist ein aufbewahrungspflichtiger Beleg '
        'und darf nicht gelöscht werden. Vorgesehen sind Storno bzw. Korrekturrechnung; '
        'ein reiner Entwurf lässt sich in der Anwendung ausblenden.'
    )


for _modell in GESCHUETZTE_BELEG_MODELLE:
    pre_delete.connect(_block_hard_delete, sender=_modell, dispatch_uid='sedo_block_hard_delete')
