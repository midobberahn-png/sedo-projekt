# Generated for v1.0 beta 3.11 Security Hardening

from django.db import migrations


def create_security_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    for name in ['SEDO Admin', 'SEDO Buchhaltung', 'SEDO Mitarbeiter Lesen']:
        Group.objects.get_or_create(name=name)


def remove_security_groups(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Group.objects.filter(name__in=['SEDO Admin', 'SEDO Buchhaltung', 'SEDO Mitarbeiter Lesen']).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('sedo', '0002_auditlog'),
    ]

    operations = [
        migrations.RunPython(create_security_groups, remove_security_groups),
    ]
