from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('sedo', '0014_invoice_lifecycle')]
    operations = [
        migrations.AddField(
            model_name='offer', name='follow_up_date',
            field=models.DateField(blank=True, db_index=True, null=True, verbose_name='Wiedervorlage am'),
        ),
        migrations.AddField(
            model_name='offer', name='follow_up_note',
            field=models.TextField(blank=True, default='', verbose_name='Wiedervorlage – Notiz'),
        ),
        migrations.AddField(
            model_name='offer', name='last_follow_up_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Zuletzt nachgefasst'),
        ),
    ]
