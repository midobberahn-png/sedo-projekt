from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters
from django.views.decorators.http import require_POST

from .dashboard_widgets import DashboardLayoutForm, layout_for
from .models import DashboardPreference
from .services.banking import BankingError, fetch_bank_balance
from .services.dashboard_weather import weather_for


@login_required
@require_POST
def save_layout(request):
    if request.POST.get('action') == 'reset':
        DashboardPreference.objects.filter(user=request.user).delete()
        messages.success(request, 'Dein Dashboard zeigt wieder die Standardauswahl.')
        return redirect('sedo:dashboard')
    form = DashboardLayoutForm(request.POST)
    if not form.is_valid():
        from .views import dashboard
        response = dashboard(request, layout_form=form)
        response.status_code = 400
        return response
    DashboardPreference.objects.update_or_create(user=request.user, defaults=form.cleaned_data)
    messages.success(request, 'Deine Kachelauswahl wurde gespeichert.')
    return redirect('sedo:dashboard')


@login_required
@require_POST
@sensitive_post_parameters('fints_pin_once')
def refresh_balance(request):
    if 'balance' not in layout_for(request.user)['widgets']:
        return HttpResponseBadRequest('Die Kontostand-Kachel ist nicht ausgewählt.')
    try:
        fetch_bank_balance(pin=request.POST.get('fints_pin_once', ''))
        messages.success(request, 'Kontostand von der Bank abgerufen.')
    except BankingError as exc:
        messages.error(request, str(exc))
    return redirect('sedo:dashboard')


@login_required
@require_POST
@never_cache
def refresh_weather(request):
    layout = layout_for(request.user)
    if 'weather' not in layout['widgets']:
        return JsonResponse({'state': 'hidden'}, status=400)
    return JsonResponse(weather_for(layout, refresh=True))
