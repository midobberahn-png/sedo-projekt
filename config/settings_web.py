"""Single-company web profile. The desktop profile remains local by default."""
import os
from pathlib import Path
import stat

from django.core.exceptions import ImproperlyConfigured
from sedo.web_origin import validate_origin


_home = os.environ.get('SEDO_HOME', '').strip()
if os.name == 'nt':
    raise ImproperlyConfigured('Der zentrale SEDO-Webserver benötigt Linux/POSIX; Windows dient als Client.')
if not _home or not Path(_home).is_absolute():
    raise ImproperlyConfigured('SEDO_HOME muss für den Webbetrieb ein absoluter, dauerhafter Datenordner sein.')
_home_path = Path(_home)
if (_home_path.is_symlink() or not _home_path.is_dir()
        or stat.S_IMODE(_home_path.stat().st_mode) & 0o077):
    raise ImproperlyConfigured('SEDO_HOME muss bereits als privater Ordner (Modus 0700) existieren.')

from .settings import *  # noqa: F403,F401 - shared application configuration
from .settings import _env_bool


if SEDO_HOME.resolve() == BASE_DIR.resolve():  # noqa: F405
    raise ImproperlyConfigured('Web-Daten dürfen nicht im Quellcode-Verzeichnis liegen.')
if os.environ.get('DJANGO_DEBUG', '').strip() == '1':
    raise ImproperlyConfigured('DJANGO_DEBUG muss im Webbetrieb ausgeschaltet bleiben.')
if not os.environ.get('DJANGO_SECRET_KEY', '').strip():
    raise ImproperlyConfigured('DJANGO_SECRET_KEY muss im Webbetrieb ausdrücklich gesetzt sein.')
if len(SECRET_KEY) < 50 or len(set(SECRET_KEY)) < 5 or SECRET_KEY.startswith('django-insecure-'):  # noqa: F405
    raise ImproperlyConfigured('DJANGO_SECRET_KEY benötigt einen eigenen langen Zufallswert.')

try:
    _origin = validate_origin(os.environ.get('SEDO_WEB_ORIGIN', ''))
except ValueError as exc:
    raise ImproperlyConfigured('SEDO_WEB_ORIGIN muss eine genaue HTTPS-Domain ohne Pfad sein.') from exc
_host = _origin.removeprefix('https://')

DEBUG = False
SEDO_LOCAL_ONLY = False
SEDO_WEB_MODE = True
SEDO_WEB_AI_ENABLED = _env_bool('SEDO_WEB_AI_ENABLED', '0')  # noqa: F405
SEDO_WEB_STT_ENABLED = _env_bool('SEDO_WEB_STT_ENABLED', '0')  # noqa: F405
ALLOWED_HOSTS = [_host]
CSRF_TRUSTED_ORIGINS = [_origin]
# web.py binds only to loopback behind the HTTPS reverse proxy. Waitress uses
# trusted, overwritten proxy headers for scheme and client address; Django
# must not process forwarding headers a second time.
SEDO_TRUSTED_PROXIES = []
SECURE_PROXY_SSL_HEADER = None
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
STATIC_ROOT = SEDO_HOME / 'staticfiles'  # noqa: F405
STATIC_URL = '/static/'
WHITENOISE_USE_FINDERS = False
WHITENOISE_AUTOREFRESH = False
DATABASES['default']['OPTIONS'] = {'timeout': 20}  # noqa: F405
