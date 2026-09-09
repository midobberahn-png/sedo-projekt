from django.contrib import admin
from .models import Customer, CustomerObject, Invoice, InvoiceItem, Offer, OfferItem, ServiceCatalogItem, Payment, CashbookEntry, AareonMapping, DunningDocument, AppSetting, AuditLog

class NurLesenMixin:
    """Macht eine Admin-Ansicht schreibgeschützt (Review-Fund F-02).

    Der Admin hatte für Rechnungen, Angebote, Zahlungen, Mahnungen und das
    Audit-Log keinerlei Löschsperre: „Ausgewählte löschen" entfernte einen
    Beleg samt Positionen und Mahnungen endgültig aus der Datenbank – ohne
    Protokolleintrag und mit einer Nummernlücke als Folge. Für GoBD-relevante
    Belege ist der Admin damit eine Einsichtsansicht, kein Werkzeug.

    Der Wächter in ``sedo/signals.py`` fängt denselben Fall auf Modellebene ab;
    dieses Mixin sorgt dafür, dass der Knopf gar nicht erst erscheint.

    ``obj=None`` als Vorgabe, damit das Mixin auf ModelAdmin **und** Inline passt.
    """

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SoftDeleteSichtbarMixin:
    """Zeigt im Admin auch ausgeblendete Belege.

    Der Docstring von ``SoftDeleteModel`` verspricht, dass ausgeblendete
    Datensätze für Admin und Audit über ``all_objects`` erreichbar bleiben.
    Der Admin nutzte aber den Standard-Manager und zeigte sie deshalb nirgends
    an – der Soft-Delete erfüllte seinen Zweck nur halb (F-02).

    Nur für ``Invoice``, ``Offer`` und ``Payment`` verwendbar; nur diese drei
    Modelle erben von ``SoftDeleteModel`` und haben ``all_objects``.
    """

    def get_queryset(self, request):
        qs = self.model.all_objects.get_queryset()
        ordering = self.get_ordering(request)
        if ordering:
            qs = qs.order_by(*ordering)
        return qs


class CustomerObjectInline(admin.TabularInline):
    model = CustomerObject
    extra = 0

    def has_delete_permission(self, request, obj=None):
        # Ein gelöschtes Objekt setzt über SET_NULL den Leistungsort auf ALLEN
        # bereits ausgestellten Rechnungen still auf leer – ohne Protokoll.
        # Die Anwendung bietet den Weg selbst an (Kundendetail), dort mit
        # Rückfrage; hier fehlte jede Sicherung.
        return False

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name','customer_no','customer_type','email','city')
    search_fields = ('name','customer_no','email','city')
    inlines = [CustomerObjectInline]

class InvoiceItemInline(NurLesenMixin, admin.TabularInline):
    model = InvoiceItem
    extra = 0

@admin.register(Invoice)
class InvoiceAdmin(SoftDeleteSichtbarMixin, NurLesenMixin, admin.ModelAdmin):
    list_display = ('number','customer','issue_date','gross_total','status','invoice_type','deleted_at')
    search_fields = ('number','customer__name')
    list_filter = ('status','invoice_type','issue_date','deleted_at')
    inlines = [InvoiceItemInline]

class OfferItemInline(NurLesenMixin, admin.TabularInline):
    model = OfferItem
    extra = 0

@admin.register(Offer)
class OfferAdmin(SoftDeleteSichtbarMixin, NurLesenMixin, admin.ModelAdmin):
    list_display = ('number','customer','issue_date','gross_total','status','deleted_at')
    search_fields = ('number','customer__name')
    list_filter = ('status','issue_date','deleted_at')
    inlines = [OfferItemInline]


@admin.register(Payment)
class PaymentAdmin(SoftDeleteSichtbarMixin, NurLesenMixin, admin.ModelAdmin):
    list_display = ('invoice', 'booking_date', 'amount', 'payer_name', 'deleted_at')
    list_filter = ('booking_date', 'deleted_at')
    search_fields = ('invoice__number', 'payer_name', 'purpose')


@admin.register(DunningDocument)
class DunningDocumentAdmin(NurLesenMixin, admin.ModelAdmin):
    # Kein SoftDeleteSichtbarMixin und kein 'deleted_at': DunningDocument ist
    # ein TimeStampedModel ohne all_objects – beides würde den Systemcheck
    # beim Start abbrechen lassen.
    list_display = ('invoice', 'kind', 'document_date', 'amount_open')
    list_filter = ('kind', 'document_date')
    search_fields = ('invoice__number',)


@admin.register(CashbookEntry)
class CashbookEntryAdmin(admin.ModelAdmin):
    """Kassenbuch: Löschen gesperrt, Ändern bewusst noch erlaubt.

    Anders als bei den übrigen Belegen bleibt hier die Bearbeitung offen: Die
    Anwendung kennt keine Korrekturbuchung, der Admin ist bis auf Weiteres der
    einzige Weg, einen Zahlendreher zu berichtigen. Sobald die App eine
    Korrekturbuchung anbietet (Review-Fund F-69), gehört auch hier der
    NurLesenMixin davor.
    """

    list_display = ('booking_no', 'booking_date', 'kind', 'amount', 'name', 'is_locked')
    list_filter = ('kind', 'booking_date', 'is_locked')
    search_fields = ('booking_no', 'name', 'text')

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(AareonMapping)
admin.site.register(AppSetting)


@admin.register(AuditLog)
class AuditLogAdmin(NurLesenMixin, admin.ModelAdmin):
    list_display = ('created_at', 'user', 'action', 'object_type', 'object_repr', 'ip_address')
    list_filter = ('action', 'object_type', 'created_at')
    search_fields = ('object_repr', 'message', 'user__username', 'ip_address')
    readonly_fields = ('created_at', 'updated_at', 'user', 'action', 'object_type', 'object_id', 'object_repr', 'message', 'ip_address', 'user_agent', 'metadata')


@admin.register(ServiceCatalogItem)
class ServiceCatalogItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'default_unit', 'created_by_ai', 'updated_at']
    search_fields = ['name']
