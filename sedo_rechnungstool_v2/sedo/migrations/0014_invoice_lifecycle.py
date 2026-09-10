from django.db import migrations, models


def forward(apps, schema_editor):
    Invoice = apps.get_model('sedo', 'Invoice')
    Invoice.objects.filter(status='entwurf').update(lifecycle='draft')
    Invoice.objects.filter(status='korrigiert').update(lifecycle='corrected')
    Invoice.objects.filter(status='storniert').update(lifecycle='cancelled')


def backward(apps, schema_editor):
    # Legacy-Status bleibt während H3 bestehen; Rückmigration darf fachliche
    # Zahlungs-/Mahninformationen nicht aus lifecycle rekonstruieren/überschreiben.
    return


class Migration(migrations.Migration):
    dependencies = [('sedo', '0013_invoice_query_indexes')]
    operations = [
        migrations.AddField(
            model_name='invoice',
            name='lifecycle',
            field=models.CharField(
                choices=[('draft','Entwurf'),('issued','Ausgestellt'),('corrected','Ersetzt durch Korrektur'),('cancelled','Storniert')],
                default='issued', max_length=16, verbose_name='Belegzustand',
            ),
        ),
        migrations.RunPython(forward, backward),
        migrations.AddConstraint(
            model_name='invoice',
            constraint=models.CheckConstraint(
                condition=(~models.Q(status__in=('entwurf','korrigiert','storniert'))
                           | models.Q(status='entwurf', lifecycle='draft')
                           | models.Q(status='korrigiert', lifecycle='corrected')
                           | models.Q(status='storniert', lifecycle='cancelled')),
                name='invoice_lifecycle_legacy_consistent',
            ),
        ),
        migrations.RemoveConstraint(model_name='invoice', name='invoice_unique_gwg_monthly_run'),
        migrations.AddConstraint(
            model_name='invoice',
            constraint=models.UniqueConstraint(
                fields=('customer','obj','period_start','period_end'),
                condition=(models.Q(source='GWG Monatsrechnung') & models.Q(deleted_at__isnull=True) & ~models.Q(lifecycle='cancelled')),
                name='invoice_unique_gwg_monthly_run',
            ),
        ),
    ]
