from django.contrib import admin
from .models import Customer, CustomerObject, Invoice, InvoiceItem, Offer, OfferItem, ServiceCatalogItem, Payment, CashbookEntry, AareonMapping, DunningDocument, AppSetting, AuditLog

class CustomerObjectInline(admin.TabularInline):
    model = CustomerObject
    extra = 0

@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ('name','customer_no','customer_type','email','city')
    search_fields = ('name','customer_no','email','city')
    inlines = [CustomerObjectInline]

class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem
    extra = 0

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ('number','customer','issue_date','gross_total','status','invoice_type')
    search_fields = ('number','customer__name')
    list_filter = ('status','invoice_type','issue_date')
    inlines = [InvoiceItemInline]

class OfferItemInline(admin.TabularInline):
    model = OfferItem
    extra = 0

@admin.register(Offer)
class OfferAdmin(admin.ModelAdmin):
    list_display = ('number','customer','issue_date','gross_total','status')
    search_fields = ('number','customer__name')
    list_filter = ('status','issue_date')
    inlines = [OfferItemInline]

admin.site.register(Payment)
admin.site.register(CashbookEntry)
admin.site.register(AareonMapping)

admin.site.register(DunningDocument)
admin.site.register(AppSetting)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'user', 'action', 'object_type', 'object_repr', 'ip_address')
    list_filter = ('action', 'object_type', 'created_at')
    search_fields = ('object_repr', 'message', 'user__username', 'ip_address')
    readonly_fields = ('created_at', 'updated_at', 'user', 'action', 'object_type', 'object_id', 'object_repr', 'message', 'ip_address', 'user_agent', 'metadata')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(ServiceCatalogItem)
class ServiceCatalogItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'default_unit', 'created_by_ai', 'updated_at']
    search_fields = ['name']
