from django.conf import settings
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect
from django.utils.http import urlencode

from .backup_utils import ensure_weekly_backup
from .company_profile import SellerProfileError, load_profile
from .security import (ADMIN_VIEWS, READ_ONLY_VIEWS, SAFE_METHODS, LoginThrottle,
                       LoginThrottleUnavailable, is_administrator, is_read_only,
                       trusted_proxy)


class TrustedProxyHeadersMiddleware:
    """Only explicitly trusted socket peers may assert transport/client headers."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not trusted_proxy(request.META.get('REMOTE_ADDR', '')):
            for key in ('HTTP_X_FORWARDED_PROTO', 'HTTP_X_FORWARDED_FOR', 'HTTP_X_FORWARDED_HOST'):
                request.META.pop(key, None)
        return self.get_response(request)


class LoginThrottleMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.throttle = LoginThrottle()

    def __call__(self, request):
        response = self.get_response(request)
        keys = getattr(request, '_sedo_login_attempt', None)
        if keys and request.user.is_authenticated and 300 <= response.status_code < 400:
            self.throttle.succeeded(keys)
        return response

    def process_view(self, request, view_func, view_args, view_kwargs):
        match = request.resolver_match
        if request.method != 'POST' or not match or match.view_name not in ('login', 'admin:login'):
            return None
        try:
            keys, retry = self.throttle.reserve(request)
        except LoginThrottleUnavailable:
            response = HttpResponse('Anmeldung vorübergehend nicht verfügbar. Bitte später erneut versuchen.', status=503, content_type='text/plain; charset=utf-8')
            response['Retry-After'] = '60'
            response['Cache-Control'] = 'no-store'
            return response
        if retry:
            response = HttpResponse('Zu viele Anmeldeversuche. Bitte nach der angegebenen Wartezeit erneut versuchen.', status=429, content_type='text/plain; charset=utf-8')
            response['Retry-After'] = str(retry)
            response['Cache-Control'] = 'no-store'
            return response
        request._sedo_login_attempt = keys
        return None


class RoleRequiredMiddleware:
    """Enforce permissions before any view, including stateful GET handlers."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        if not user or not user.is_authenticated:
            return None
        match = request.resolver_match
        name = match.view_name if match else ''
        if name in ('login', 'logout'):
            return None
        if name in ADMIN_VIEWS and not is_administrator(user):
            return HttpResponseForbidden('Für diese Aktion ist ein Administrationskonto erforderlich.')
        if is_read_only(user) and (request.method not in SAFE_METHODS or name not in READ_ONLY_VIEWS):
            return HttpResponseForbidden('Dieses Konto hat nur Leserechte. Diese Aktion ist nicht freigegeben.')
        return None


class CompanySetupMiddleware:
    """Keep a new installation in onboarding until its own sender is valid."""
    RECOVERY_VIEWS = frozenset({'sedo:company_setup', 'sedo:backup',
                                'sedo:readiness', 'sedo:security_status'})
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        user = getattr(request, 'user', None)
        match = request.resolver_match
        name = match.view_name if match else ''
        if not user or not user.is_authenticated or not name.startswith('sedo:') or name in self.RECOVERY_VIEWS:
            return None
        try:
            load_profile()
        except SellerProfileError:
            response = redirect('sedo:company_setup')
            response['Cache-Control'] = 'no-store'
            return response
        return None


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
        # Abmelden ohne gültige Sitzung darf nicht auf die Anmeldeseite
        # umleiten: LoginView springt danach per GET auf /logout/ – und das
        # beantwortet Django 6 mit HTTP 405. Für den Nutzer sähe es aus, als
        # sei das Abmelden weiterhin kaputt (Review-Fund F-53). Der CSRF-Schutz
        # bleibt davon unberührt, hier entfällt nur die Anmeldepflicht.
        '/logout/',
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
                return redirect(f'{login_url}?{urlencode({"next": request.get_full_path()})}')
        return self.get_response(request)


class WeeklyBackupMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self._checked = False

    def __call__(self, request):
        if not self._checked and not is_read_only(getattr(request, 'user', None)):
            self._checked = True
            ensure_weekly_backup()
        return self.get_response(request)
