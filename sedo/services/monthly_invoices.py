"""Erzeugung des GWG-Monatslaufs; HTTP-Eingaben werden in der View geprüft."""

import logging
from decimal import Decimal

from django.db import IntegrityError, transaction

from ..models import AareonMapping, CustomerObject, Invoice, InvoiceItem
from .calc import recalc_invoice
from .invoice_files import datei_rueckweg
from .invoices import generate_invoice_files
from .numbering import create_numbered_invoice

logger = logging.getLogger('sedo')


def generate_gwg_monthly(*, customer, month, issue_date, due_date,
                         period_start, period_end, services, skip_existing,
                         limit, request):
    """Jede Einheit atomar abrechnen und einzelne Fehlschläge sichtbar halten."""
    created = skipped = missing = 0
    errors = []
    created_ids = []
    units = list(AareonMapping.objects.values_list(
        'billing_unit_sender', flat=True).distinct().order_by('billing_unit_sender'))
    if limit:
        units = units[:limit]
    # Mapping einmal laden statt je Einheit und Leistung wiederholt abzufragen.
    mapping_index = {
        (mapping.billing_unit_sender, mapping.service_id): mapping
        for mapping in AareonMapping.objects.all()
    }
    for unit_name in units:
        if skip_existing and Invoice.objects.filter(
                customer=customer, obj__name=unit_name, source='GWG Monatsrechnung',
                period_start=period_start, period_end=period_end,
        ).exclude(lifecycle='cancelled').exists():
            skipped += 1
            continue
        # Ohne passende Leistung keine Nummer und kein Objekt reservieren.
        matched = []
        for service in services:
            mapping = mapping_index.get((unit_name, service['id']))
            if mapping:
                matched.append((service, mapping))
            else:
                missing += 1
        if not matched:
            continue
        try:
            with datei_rueckweg() as merken, transaction.atomic():
                obj, _ = CustomerObject.objects.get_or_create(
                    customer=customer,
                    name=unit_name,
                    defaults={
                        'street': unit_name, 'postcode': customer.postcode,
                        'city': customer.city, 'country': customer.country or 'DE',
                    },
                )
                inv = create_numbered_invoice(
                    invoice_type='380', customer=customer, obj=obj,
                    issue_date=issue_date, due_date=due_date,
                    period_start=period_start, period_end=period_end,
                    vat_rate=Decimal('19.00'), status='erstellt',
                    source='GWG Monatsrechnung',
                    note=f'Monatsrechnung {month} auf Basis GWG-Mapping.',
                )
                for position, (service, mapping) in enumerate(matched, start=1):
                    InvoiceItem.objects.create(
                        invoice=inv, position=position,
                        description=service['description'],
                        quantity=service['quantity'], unit=service['unit'],
                        unit_price=service['unit_price'],
                        article_no=mapping.nksl_internal,
                        leistungsart_id=mapping.service_id,
                        leistungsart_text=mapping.service_id,
                    )
                recalc_invoice(inv)
                inv.save()
                merken(inv)
                generate_invoice_files(inv, erstmalig=True, request=request)
            created += 1
            created_ids.append(inv.id)
        except IntegrityError:
            # Ein zweiter Lauf kann dieselbe Einheit parallel belegen.
            logger.info('GWG-Monatslauf: Einheit %s existiert bereits (übersprungen)', unit_name)
            skipped += 1
        except Exception as exc:
            logger.exception('GWG-Monatslauf: Einheit %s fehlgeschlagen', unit_name)
            errors.append(f'{unit_name}: {exc}')
    return {'created': created, 'skipped': skipped, 'missing': missing,
            'errors': errors[:20], 'month': month, 'invoice_ids': created_ids}
