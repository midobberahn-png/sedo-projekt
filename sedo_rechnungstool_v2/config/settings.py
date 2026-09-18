from pathlib import Path
import os
import sys
import tempfile

BASE_DIR = Path(__file__).resolve().parent.parent

# Als gebündeltes Desktop-Programm (PyInstaller) liegen die Programmdateien in
# einem schreibgeschützten Bundle – Code/Templates/Static kommen dann aus dem
# entpackten Bundle-Verzeichnis (sys._MEIPASS), während alle SCHREIBENDEN
# Pfade (Datenbank, Medien, Backups, .env, Secret-Key) über SEDO_HOME in einen
# Benutzerordner zeigen (setzt desktop.py). Im normalen Betrieb bleibt alles
# wie gehabt: SEDO_HOME = Projektordner.
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(getattr(sys, '_MEIPASS', BASE_DIR))
SEDO_HOME = Path(os.environ.get('SEDO_HOME', BASE_DIR))


def _load_dotenv(path: Path) -> None:
    """Lädt Schlüssel=Wert-Zeilen aus einer .env-Datei (ohne Zusatzpaket).

    Bereits gesetzte Umgebungsvariablen behalten Vorrang. Damit wirkt die
    .env nach dem Muster von .env.example jetzt wirklich – vorher wurde sie
    von nichts eingelesen.
    """
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(SEDO_HOME / '.env')

# Sicherer Default: DEBUG ist AUS. Entwicklung explizit per DJANGO_DEBUG=1
# (Umgebungsvariable oder .env). Statische Dateien liefert WhiteNoise auch
# ohne DEBUG aus, Medien (Rechnungs-PDFs) die eigene Media-Route in urls.py.
DEBUG = os.environ.get('DJANGO_DEBUG', '0') == '1'


def _get_secret_key() -> str:
    """SECRET_KEY ohne festen Fallback im Quellcode.

    Reihenfolge: Umgebungsvariable (bzw. .env) → einmalig erzeugter, lokal
    persistierter Schlüssel in ``data/.secret_key``. So gibt es auch im
    Einzelplatzbetrieb keinen dokumentierten Standard-Schlüssel mehr.
    """
    key = os.environ.get('DJANGO_SECRET_KEY', '').strip()
    if key:
        return key
    key_file = SEDO_HOME / 'data' / '.secret_key'
    try:
        stored = key_file.read_text(encoding='utf-8').strip()
        if stored:
            return stored
    except OSError:
        pass
    from django.core.management.utils import get_random_secret_key
    key = get_random_secret_key()
    try:
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_text(key, encoding='utf-8')
        os.chmod(key_file, 0o600)
    except OSError:
        pass  # z. B. schreibgeschützt – Schlüssel gilt dann nur für diesen Prozess
    return key


SECRET_KEY = _get_secret_key()

# Im lokalen Entwicklungs-/Einzelplatzbetrieb (DEBUG) ist '*' für den
# Heimnetz-Zugriff bequem. Im Produktivbetrieb (DEBUG=0) gibt es KEINEN
# Wildcard-Fallback mehr – Hosts müssen explizit über DJANGO_ALLOWED_HOSTS
# bzw. die .env gesetzt werden.
_default_hosts = '127.0.0.1,localhost,0.0.0.0' + (',*' if DEBUG else '')
ALLOWED_HOSTS = [h.strip() for h in os.environ.get('DJANGO_ALLOWED_HOSTS', _default_hosts).split(',') if h.strip()]

def _env_bool(name: str, default: str = '0') -> bool:
    return os.environ.get(name, default).strip().lower() in ('1', 'true', 'yes', 'ja', 'on')

# Die Desktop-App lauscht ausschließlich auf dem eigenen Rechner. In diesem
# Modus ist lokales HTTP korrekt; Secure-Cookies/HSTS setzen zwingend HTTPS
# voraus und würden sonst die Anmeldung blockieren. Für einen Netzbetrieb muss
# der Modus bewusst abgeschaltet und HTTPS vollständig konfiguriert werden.
SEDO_LOCAL_ONLY = _env_bool('SEDO_LOCAL_ONLY', '1')

# Temporärer Testbetrieb über Cloudflare Quick Tunnel / lokale Netze.
# Wichtig für POST-Formulare über https://*.trycloudflare.com, sonst blockiert Django CSRF.
CSRF_TRUSTED_ORIGINS = [
    'https://*.trycloudflare.com',
    'https://*.cloudflareaccess.com',
    'http://127.0.0.1:8000',
    'http://localhost:8000',
]
_extra_csrf = os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').strip()
if _extra_csrf:
    CSRF_TRUSTED_ORIGINS += [x.strip() for x in _extra_csrf.split(',') if x.strip()]

# Für Tunnel/Proxy-Betrieb: Django erkennt HTTPS über den Forwarded-Header.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'sedo',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # WhiteNoise: statische Dateien auch bei DEBUG=0 (runserver liefert sie
    # sonst nicht aus – die Desktop-Starter liefen dann ohne CSS/JS).
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'sedo.middleware.LoginRequiredMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'sedo.middleware.WeeklyBackupMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'sedo' / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': SEDO_HOME / 'data' / 'sedo_web.sqlite3',
    }
}

LANGUAGE_CODE = 'de-de'
TIME_ZONE = 'Europe/Berlin'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATICFILES_DIRS = [BASE_DIR / 'sedo' / 'static']
# WhiteNoise im Einzelplatzbetrieb: direkt aus den App-Static-Ordnern
# ausliefern (kein collectstatic-Schritt für den Endnutzer nötig) und
# Änderungen ohne Neustart aufnehmen.
WHITENOISE_USE_FINDERS = True
WHITENOISE_AUTOREFRESH = True
MEDIA_URL = 'media/'
MEDIA_ROOT = SEDO_HOME / 'media'

# Zielordner der Sicherungen. Bisher leitete backup_utils.backup_dir() diesen
# Pfad selbst aus SEDO_HOME ab; als Setting lässt er sich für Testläufe
# umlenken (Review-Fund F-38).
SEDO_BACKUP_DIR = SEDO_HOME / 'backups'

# Testläufe schreiben ihre PDFs/XMLs in einen eigenen Ordner, damit sie keine
# echten Dateien im media/-Ordner überschreiben (die Test-DB war schon immer
# getrennt – der Medienordner bisher nicht).
# Gleiches gilt seit Review-Fund F-38 für die Sicherungen: Jede TestCase-Klasse
# startet mit einer frischen Test-DB ohne 'last_weekly_backup_at', deshalb legte
# die WeeklyBackupMiddleware pro Testklasse ein ZIP im echten backups/-Ordner an
# (~20 pro Lauf). Beides landet jetzt im Temp-Verzeichnis.
if 'test' in sys.argv:
    MEDIA_ROOT = Path(tempfile.gettempdir()) / 'sedo_test_media'
    SEDO_BACKUP_DIR = Path(tempfile.gettempdir()) / 'sedo_test_backups'

# Deutsche Zahlformatierung in der Oberfläche: 4.382,20 statt 4382,20.
# Achtung: Zahlen, die in URLs/JS/Formular-values landen (z. B. {{ obj.pk }}),
# müssen mit |unlocalize ausgegeben werden, sonst wird ab 1000 gruppiert!
USE_THOUSAND_SEPARATOR = True

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# Für echte Veröffentlichung später aktivieren: DEBUG=False, echte SECRET_KEY,
# ALLOWED_HOSTS einschränken, HTTPS/Proxy sauber setzen und manage.py check --deploy ausführen.


# Production-ready security switches. In local development these remain conservative/off by default.
STATIC_ROOT = BASE_DIR / 'staticfiles'
SECURE_SSL_REDIRECT = _env_bool('DJANGO_SECURE_SSL_REDIRECT', '0')
SESSION_COOKIE_SECURE = _env_bool('DJANGO_SESSION_COOKIE_SECURE', '0')
CSRF_COOKIE_SECURE = _env_bool('DJANGO_CSRF_COOKIE_SECURE', '0')
SECURE_HSTS_SECONDS = int(os.environ.get('DJANGO_SECURE_HSTS_SECONDS', '0') or '0')
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('DJANGO_SECURE_HSTS_INCLUDE_SUBDOMAINS', '0')
SECURE_HSTS_PRELOAD = _env_bool('DJANGO_SECURE_HSTS_PRELOAD', '0')
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SESSION_COOKIE_HTTPONLY = True
# HttpOnly ist hier gefahrlos: alle eigenen Skripte (offer_ai, magic_tone,
# plausibility …) lesen den CSRF-Token aus dem Formularfeld im DOM, nicht
# aus dem Cookie (Audit-Empfehlung 15.08.).
CSRF_COOKIE_HTTPONLY = True
