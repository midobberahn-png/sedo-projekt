"""Administrative boundary for legacy system screens; finance remains shared."""
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters

from . import views
from .audit import write_audit
from .services.credential_binding import CredentialError, save_config


def staff_required(view):
    @wraps(view)
    @login_required
    def guarded(request, *args, **kwargs):
        if not request.user.is_active or not (request.user.is_staff or request.user.is_superuser):
            return HttpResponseForbidden('Administration erforderlich.')
        return view(request, *args, **kwargs)
    return never_cache(guarded)


@staff_required
@sensitive_post_parameters('smtp_password', 'fints_pin', 'weather_api_key', 'ai_api_key')
def settings_page(request):
    """Intercept credential writes; retain established UI and other admin actions."""
    action = request.POST.get('action') if request.method == 'POST' else None
    if action not in ('save_smtp', 'save_banking'):
        return views.settings_page(request)
    service = 'smtp' if action == 'save_smtp' else 'fints'
    keys = ('host', 'port', 'user', 'from_name') if service == 'smtp' else ('blz', 'endpoint', 'login', 'iban', 'product_id')
    config = {key: request.POST.get(service+'_'+key, '') for key in keys}
    if service == 'smtp':
        config['tls'] = bool(request.POST.get('smtp_tls'))
    secret_key = 'smtp_password' if service == 'smtp' else 'fints_pin'
    try:
        revoked = save_config(service, config, request.POST.get(secret_key, ''), clear=bool(request.POST.get(secret_key+'_clear')))
    except CredentialError as exc:
        messages.error(request, str(exc))
    else:
        write_audit(request, 'update', object_type='AppSetting', object_repr=service.upper(),
                    message='Verbindungseinstellungen mit Identitätsbindung gespeichert')
        messages.success(request, 'Verbindungseinstellungen gespeichert.')
        if revoked:
            messages.warning(request, 'Die Verbindungsidentität wurde geändert. Bitte das Passwort bzw. die PIN erneut eingeben.')
    return redirect('sedo:settings')


@staff_required
def backup_page(request):
    # Authorization precedes even listing, inspection and confirmation handling.
    return views.backup_page(request)
