"""Etappe 7: fehlende/falsche Indizes müssen echte Datenbank-Assertions brechen."""
import gegenproben_entwurf as runner

runner.TEST = 'sedo.tests.test_invoice_indexes.InvoiceIndexDatenbankTests.'
MIGRATION = 'sedo/migrations/0013_invoice_query_indexes.py'
runner.MUTATIONEN = [
    ('statusindex_entfernt', MIGRATION,
     "        migrations.AddIndex(\n            model_name='invoice',\n            index=models.Index(fields=['status', 'due_date'], name='invoice_status_due_idx'),\n        ),\n",
     '', 'test_statusindex_physisch'),
    ('faelligkeitsindex_entfernt', MIGRATION,
     "        migrations.AddIndex(\n            model_name='invoice',\n            index=models.Index(condition=models.Q(('deleted_at__isnull', True)), fields=['due_date', 'status'], name='invoice_active_due_status_idx'),\n        ),\n",
     '', 'test_faelligkeitsindex_physisch'),
    ('teilindex_bedingung_entfernt', MIGRATION,
     "condition=models.Q(('deleted_at__isnull', True)), ",
     '', 'test_faelligkeitsindex_physisch'),
    ('falsche_spaltenreihenfolge', MIGRATION,
     "fields=['due_date', 'status']", "fields=['status', 'due_date']",
     'test_faelligkeitsindex_physisch'),
]

if __name__ == '__main__':
    raise SystemExit(runner.main())
