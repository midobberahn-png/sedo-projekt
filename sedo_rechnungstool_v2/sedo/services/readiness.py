from django.conf import settings
from django.contrib.auth import get_user_model

from sedo.audit import AUDIT_ERROR_KEY
from sedo.models import AareonMapping, AppSetting
from sedo.services.banking import banking_is_configured
from sedo.services.belegketten import pruefe_belegketten
from sedo.services.mail import smtp_is_configured


MANUAL_CHECKS = {
    'windows_acceptance': 'Windows-Abnahme',
    'zugferd_external_validation': 'ZUGFeRD extern validiert',
    'restore_test': 'Backup-Rücksicherung getestet',
}


def is_local_only_mode() -> bool:
    loopback_hosts = {'127.0.0.1', 'localhost', '0.0.0.0', '[::1]', 'testserver'}
    configured_hosts = set(getattr(settings, 'ALLOWED_HOSTS', []))
    return bool(getattr(settings, 'SEDO_LOCAL_ONLY', True)) and configured_hosts.issubset(loopback_hosts)


def transport_security_checks() -> list[dict]:
    local = is_local_only_mode()
    if local:
        note = 'Im lokalen Einzelplatzbetrieb korrekt; die App ist nur auf diesem Rechner erreichbar.'
        return [
            {'label': 'HTTPS-Redirect', 'ok': True, 'value': 'lokal nicht erforderlich', 'hint': note},
            {'label': 'Sichere Session-Cookies', 'ok': True, 'value': 'lokal passend', 'hint': 'Secure-Cookies benötigen HTTPS und würden die lokale Anmeldung sonst verhindern.'},
            {'label': 'Sichere CSRF-Cookies', 'ok': True, 'value': 'lokal passend', 'hint': 'Der Schutz bleibt aktiv; nur die HTTPS-Bindung des Cookies entfällt lokal.'},
            {'label': 'HSTS', 'ok': True, 'value': 'lokal nicht erforderlich', 'hint': 'HSTS gehört zu einer HTTPS-Netzinstallation.'},
        ]
    return [
        {'label': 'HTTPS-Redirect aktiv', 'ok': bool(settings.SECURE_SSL_REDIRECT), 'value': 'aktiv' if settings.SECURE_SSL_REDIRECT else 'nicht aktiv', 'hint': 'Netzbetrieb muss HTTP auf HTTPS umleiten.'},
        {'label': 'Sichere Session-Cookies', 'ok': bool(settings.SESSION_COOKIE_SECURE), 'value': 'aktiv' if settings.SESSION_COOKIE_SECURE else 'nicht aktiv', 'hint': 'Session-Cookies im Netz nur per HTTPS übertragen.'},
        {'label': 'Sichere CSRF-Cookies', 'ok': bool(settings.CSRF_COOKIE_SECURE), 'value': 'aktiv' if settings.CSRF_COOKIE_SECURE else 'nicht aktiv', 'hint': 'CSRF-Cookies im Netz nur per HTTPS übertragen.'},
        {'label': 'HSTS aktiv', 'ok': int(settings.SECURE_HSTS_SECONDS or 0) > 0, 'value': f'{settings.SECURE_HSTS_SECONDS} Sekunden', 'hint': 'Nach erfolgreichem HTTPS-Test schrittweise aktivieren.'},
    ]


def readiness_report() -> list[dict]:
    admin = get_user_model().objects.filter(username='admin').first()
    chains = pruefe_belegketten()['zusammenfassung']
    mapping_used = AareonMapping.objects.exists()
    seller_ok = bool(AppSetting.get('aareon_seller_id', ''))
    items = [
        {'key': 'password', 'label': 'Admin-Passwort geändert', 'ok': not (admin and admin.check_password('sedo1234')), 'group': 'Pflicht', 'href': '/admin/password_change/', 'hint': 'Schützt den Zugang zur App.'},
        {'key': 'debug', 'label': 'Produktivmodus aktiv', 'ok': not settings.DEBUG, 'group': 'Pflicht', 'href': '/sicherheit/', 'hint': 'DEBUG muss ausgeschaltet sein.'},
        {'key': 'transport', 'label': 'Betriebsart sicher konfiguriert', 'ok': all(row['ok'] for row in transport_security_checks()), 'group': 'Pflicht', 'href': '/sicherheit/', 'hint': 'Lokaler Einzelplatzbetrieb oder vollständiges HTTPS.'},
        {'key': 'backup', 'label': 'Backup vorhanden', 'ok': bool(AppSetting.get('last_backup_at', '')), 'group': 'Pflicht', 'href': '/backup/', 'hint': 'Vor dem Echtbetrieb eine aktuelle Sicherung erstellen.'},
        {'key': 'audit', 'label': 'Protokoll fehlerfrei', 'ok': not AppSetting.get(AUDIT_ERROR_KEY, ''), 'group': 'Pflicht', 'href': '/sicherheit/', 'hint': 'Kritische Aktionen müssen protokolliert werden.'},
        {'key': 'chains', 'label': 'Belegketten fehlerfrei', 'ok': chains['kettenfehler'] == 0, 'group': 'Pflicht', 'href': '/sicherheit/', 'hint': f"{chains['kettenfehler']} Fehler gefunden."},
        {'key': 'aareon', 'label': 'Aareon/ARP-Daten vollständig', 'ok': (not mapping_used) or seller_ok, 'group': 'Bei GWG', 'href': '/einstellungen/', 'hint': 'Erforderlich, sobald ein Aareon-Mapping genutzt wird.'},
        {'key': 'smtp', 'label': 'E-Mail-Versand eingerichtet', 'ok': smtp_is_configured(), 'group': 'Optional', 'href': '/einstellungen/', 'hint': 'Für direkten Rechnungsversand aus der App.'},
        {'key': 'bank', 'label': 'Bankabruf eingerichtet', 'ok': banking_is_configured(), 'group': 'Optional', 'href': '/einstellungen/', 'hint': 'Kann später ergänzt werden.'},
    ]
    for key, label in MANUAL_CHECKS.items():
        items.append({'key': key, 'label': label, 'ok': AppSetting.get(f'readiness_{key}', '0') == '1', 'group': 'Abnahme', 'manual': True, 'href': '', 'hint': 'Nach erfolgreichem Praxistest hier bestätigen.'})
    return items
