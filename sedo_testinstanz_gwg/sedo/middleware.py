from django.conf import settings
from django.shortcuts import redirect

from .backup_utils import ensure_weekly_backup


class LoginRequiredMiddleware:
    """Erzwingt Login für die App, lässt nur Login/Admin/Static passieren.

    Seit v2.0.1 ist /media/ NICHT mehr ausgenommen (vierter externer Review):
    Rechnungs-/Angebots-PDFs enthalten Geschäftsdaten und dürfen – gerade im
    Heimnetz-/Cloudflare-Tunnel-Betrieb – nur mit Login abrufbar sein. Die
    Media-Route in config/urls.py läuft durch diese Middleware, damit ist
    jeder Dateiabruf authentifiziert. /static/ (CSS/JS, keine Daten) bleibt
    frei und wird ohnehin von WhiteNoise vor dieser Middleware bedient.
    """
    EXEMPT_PREFIXES = (
        '/login/',
        '/admin/',
        '/static/',
        '/favicon.ico',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info or '/'
        if not getattr(request, 'user', None) or not request.user.is_authenticated:
            if not any(path.startswith(prefix) for prefix in self.EXEMPT_PREFIXES):
                login_url = getattr(settings, 'LOGIN_URL', '/login/')
                return redirect(f'{login_url}?next={request.get_full_path()}')
        return self.get_response(request)


class WeeklyBackupMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._checked = False

    def __call__(self, request):
        if not self._checked:
            self._checked = True
            ensure_weekly_backup()
        return self.get_response(request)
