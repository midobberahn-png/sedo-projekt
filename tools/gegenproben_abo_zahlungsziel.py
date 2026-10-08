"""F-63: Rueckkehr zu Wahrheitstests muss echte Datums-Assertions brechen.

Der bestehende Runner aendert nur temporaere Quellkopien, nie Bestandsdaten.
"""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.test_recurring_payment_days.'
SOURCE = 'sedo/views.py'
runner.MUTATIONEN = [
    ('vorlagen_null_wieder_als_fehlend', SOURCE,
     '    if payment_days is not None:', '    if payment_days:',
     'RecurringPaymentDaysTests.test_monatslauf_null_bleibt_sofort_faellig'),
    ('kunden_null_wieder_als_fehlend', SOURCE,
     '    if customer_payment_days is not None:', '    if customer_payment_days:',
     'RecurringPaymentDaysTests.test_vorlage_ohne_rechnungsfaelligkeit_uebernimmt_kunde_null'),
    ('monatslauf_umgeht_helper', SOURCE,
     '        due = issue_date + timedelta(days=_recurring_payment_days(\n            tmpl.payment_days, tmpl.customer.payment_days,\n        ))',
     '        due = issue_date + timedelta(days=tmpl.payment_days or tmpl.customer.payment_days or DEFAULT_PAYMENT_DAYS)',
     'RecurringPaymentDaysTests.test_monatslauf_null_bleibt_sofort_faellig'),
    ('vorlagenanlage_umgeht_helper', SOURCE,
     '        payment_days=_recurring_payment_days(\n            (inv.due_date - inv.issue_date).days if inv.due_date is not None else None,\n            inv.customer.payment_days,\n        ),',
     '        payment_days=(inv.due_date - inv.issue_date).days if inv.due_date else (inv.customer.payment_days or 14),',
     'RecurringPaymentDaysTests.test_vorlage_ohne_rechnungsfaelligkeit_uebernimmt_kunde_null'),
    ('standard_hart_kodiert', SOURCE,
     '    return DEFAULT_PAYMENT_DAYS\n', '    return 14\n',
     'RecurringPaymentFallbackTests.test_beide_fehlend_nimmt_konfigurierte_konstante'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
