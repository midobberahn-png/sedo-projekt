# Generated for v1.0 beta 3.11 Security Hardening

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('sedo', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('action', models.CharField(choices=[('login', 'Login'), ('logout', 'Logout'), ('create', 'Erstellt'), ('update', 'Geändert'), ('delete', 'Gelöscht'), ('generate', 'Erzeugt'), ('payment', 'Zahlung'), ('backup', 'Backup'), ('import', 'Import'), ('security', 'Sicherheit')], max_length=30, verbose_name='Aktion')),
                ('object_type', models.CharField(blank=True, max_length=80, verbose_name='Objekttyp')),
                ('object_id', models.CharField(blank=True, max_length=80, verbose_name='Objekt-ID')),
                ('object_repr', models.CharField(blank=True, max_length=255, verbose_name='Objekt')),
                ('message', models.TextField(blank=True, verbose_name='Hinweis')),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True, verbose_name='IP-Adresse')),
                ('user_agent', models.TextField(blank=True, verbose_name='User Agent')),
                ('metadata', models.JSONField(blank=True, default=dict, verbose_name='Metadaten')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sedo_audit_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Audit-Log',
                'verbose_name_plural': 'Audit-Logs',
                'ordering': ['-created_at'],
            },
        ),
    ]
