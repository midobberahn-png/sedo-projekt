"""Atomare Erzeugung einer Rechnung aus einer Abo-Vorlage."""
from django.db import transaction
from ..company_profile import seller_document
from ..models import InvoiceItem
from . import create_numbered_invoice, generate_invoice_files, recalc_invoice
from .invoice_files import datei_rueckweg


@seller_document
def generate_recurring_invoice_from_template(tmpl, issue_date, period_start, period_end, due_date):
    """Erzeugt eine Abo-Monatsrechnung – atomar: Fehler (auch bei PDF/XML)
    rollen die komplette Rechnung zurück, keine Teilrechnungen."""
    with datei_rueckweg() as merken, transaction.atomic():
        return _generate_recurring_invoice_inner(tmpl, issue_date, period_start, period_end, due_date, merken)


def _generate_recurring_invoice_inner(tmpl, issue_date, period_start, period_end, due_date, merken):
    inv = create_numbered_invoice(
        invoice_type='380',
        customer=tmpl.customer,
        obj=tmpl.obj,
        issue_date=issue_date,
        due_date=due_date,
        period_start=period_start,
        period_end=period_end,
        vat_rate=tmpl.vat_rate,
        status='erstellt',
        source='Abo Monatsrechnung',
        note=tmpl.note or f'Monatsrechnung auf Basis der Vorlage „{tmpl.title}“.',
    )
    for idx, item in enumerate(tmpl.items.all(), start=1):
        InvoiceItem.objects.create(
            invoice=inv,
            position=idx,
            description=item.description,
            long_text=item.long_text,
            quantity=item.quantity,
            unit=item.unit,
            unit_price=item.unit_price,
            article_no=item.article_no,
        )
    recalc_invoice(inv)
    inv.save()
    merken(inv)
    generate_invoice_files(inv, erstmalig=True)
    return inv
