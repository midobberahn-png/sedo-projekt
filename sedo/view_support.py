"""Gemeinsame Formular- und Anzeigehelfer für Angebots- und Rechnungsansichten."""

from decimal import Decimal, ROUND_HALF_UP

from django.db.models import Sum

from .models import Customer, CustomerObject

def _customer_form_data(customer):
    """Lesende Kopfdaten für API und initiale Wizard-Zusammenfassung."""
    return {
        'id': customer.pk,
        'salutation': customer.salutation or '',
        'customer_no': customer.customer_no or '',
        'customer_type': customer.customer_type or 'einmalig',
        'name': customer.name or '',
        'name2': customer.name2 or '',
        'contact_person': customer.contact_person or '',
        'phone': customer.phone or '',
        'email': customer.email or '',
        'street': customer.street or '',
        'postcode': customer.postcode or '',
        'city': customer.city or '',
        'country': customer.country or 'DE',
        'payment_days': customer.payment_days,
        'objects': [
            {
                'id': obj.pk,
                'name': obj.name,
                'street': obj.street or '',
                'postcode': obj.postcode or '',
                'city': obj.city or '',
                'country': obj.country or 'DE',
            }
            for obj in customer.customer_objects.order_by('name')
        ],
    }

def _sum_decimal(queryset, field: str) -> Decimal:
    """Summe eines DecimalField als saubere Decimal mit 2 Nachkommastellen.

    SQLite liefert bei ``Sum()`` über ein DecimalField einen Float zurück
    (z. B. 5274.7000000000). Diese Umwandlung sorgt für eine korrekte Anzeige.
    """
    total = queryset.aggregate(s=Sum(field))['s'] or Decimal('0.00')
    return Decimal(str(total)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

def _customer_from_invoice_form(form):
    customer = form.cleaned_data.get('customer')
    if customer:
        return customer
    name = (form.cleaned_data.get('name') or '').strip()
    if not name:
        return None
    return Customer.objects.create(
        salutation=form.cleaned_data.get('salutation') or '',
        customer_no=form.cleaned_data.get('customer_no') or '',
        customer_type=form.cleaned_data.get('customer_type') or 'einmalig',
        name=name,
        name2=form.cleaned_data.get('name2') or '',
        contact_person=form.cleaned_data.get('contact_person') or '',
        phone=form.cleaned_data.get('phone') or '',
        email=form.cleaned_data.get('email') or '',
        street=form.cleaned_data.get('street') or '',
        postcode=form.cleaned_data.get('postcode') or '',
        city=form.cleaned_data.get('city') or '',
        country=form.cleaned_data.get('country') or 'DE',
        # F-63, zweiter Fundort: ein Kunde mit Zahlungsziel 0 bekam dauerhaft 14.
        payment_days=(14 if form.cleaned_data.get('payment_days') is None
                      else form.cleaned_data.get('payment_days')),
    )

def _object_from_invoice_form(form, customer):
    obj = form.cleaned_data.get('obj')
    if obj:
        return obj
    object_name = (form.cleaned_data.get('object_name') or '').strip()
    if not object_name or not customer:
        return None
    return CustomerObject.objects.create(
        customer=customer,
        name=object_name,
        street=form.cleaned_data.get('object_street') or '',
        postcode=form.cleaned_data.get('object_postcode') or '',
        city=form.cleaned_data.get('object_city') or '',
        country=form.cleaned_data.get('object_country') or 'DE',
    )

def _preview_from_forms(form, formset, title='Entwurfsansicht'):
    items = []
    net = Decimal('0.00')
    for item_form in formset.forms:
        if not item_form.cleaned_data or item_form.cleaned_data.get('DELETE'):
            continue
        desc = item_form.cleaned_data.get('description') or ''
        if not desc:
            continue
        qty = item_form.cleaned_data.get('quantity') or Decimal('0.00')
        price = item_form.cleaned_data.get('unit_price') or Decimal('0.00')
        line_total = (qty * price).quantize(Decimal('0.01'))
        net += line_total
        items.append({
            'description': desc,
            'long_text': item_form.cleaned_data.get('long_text') or '',
            'quantity': qty,
            'unit': item_form.cleaned_data.get('unit') or '',
            'unit_price': price,
            'line_total': line_total,
            'article_no': item_form.cleaned_data.get('article_no') or '',
        })
    vat_rate = form.cleaned_data.get('vat_rate')
    if vat_rate is None:
        vat_rate = Decimal('19.00')
    vat = (net * vat_rate / Decimal('100')).quantize(Decimal('0.01'))
    return {
        'title': title,
        'customer': form.cleaned_data.get('customer'),
        'manual_customer': {k: form.cleaned_data.get(k) for k in ['salutation','customer_no','name','name2','contact_person','phone','email','street','postcode','city','country']},
        'obj': form.cleaned_data.get('obj'),
        'manual_object': {k: form.cleaned_data.get(k) for k in ['object_name','object_street','object_postcode','object_city','object_country']},
        'issue_date': form.cleaned_data.get('issue_date'),
        'period_start': form.cleaned_data.get('period_start'),
        'period_end': form.cleaned_data.get('period_end'),
        'items': items,
        'net': net.quantize(Decimal('0.01')),
        'vat_rate': vat_rate,
        'vat': vat,
        'gross': (net + vat).quantize(Decimal('0.01')),
        'note': form.cleaned_data.get('note') if 'note' in form.cleaned_data else '',
    }
