"""Security checks for the already isolated native-build self-test only."""
from io import BytesIO
from pathlib import Path
import os


def check(expected_home):
    from django.conf import settings
    home = Path(expected_home).resolve()
    if (Path(settings.SEDO_HOME).resolve() != home or not home.name.startswith('sedo_build_check_')
            or any(os.environ.get(key) for key in ('SEDO_FINTS_PIN', 'SEDO_SMTP_PASSWORD'))):
        raise RuntimeError('Security self-check requires a clean, disposable build home.')
    from django.contrib.auth import get_user_model
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError
    from django.test import Client
    from .services.credential_binding import save_config, load_config
    from .services.incoming_detection import classify_document
    from reportlab.pdfgen import canvas
    password = 'Synthetic-Build-Check-Password-2026!'
    user = get_user_model().objects.create_user('security-build-probe', password=password)
    client = Client(HTTP_HOST='localhost')
    client.force_login(user)
    checks = {'ordinary_settings_denied': client.post('/einstellungen/', {'action':'save_smtp'}).status_code == 403,
              'ordinary_restore_denied': client.post('/backup/', {'action':'stage_restore'}).status_code == 403}
    try:
        validate_password('1', user)
    except ValidationError:
        checks['password_policy'] = True
    else:
        checks['password_policy'] = False
    config = {'host':'smtp.example.invalid', 'port':587, 'user':'build@example.invalid', 'tls':True, 'from_name':'Build'}
    save_config('smtp', config, password)
    checks['bound_secret_available'] = load_config('smtp')['password'] == password
    config['host'] = 'different.example.invalid'
    save_config('smtp', config)
    checks['retargeted_secret_revoked'] = not load_config('smtp')['password']
    anonymous = Client(HTTP_HOST='localhost')
    for _ in range(5):
        anonymous.post('/login/', {'username':user.username, 'password':'wrong'})
    checks['login_throttle'] = anonymous.post('/admin/login/', {'username':user.username, 'password':password}).status_code == 429
    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(30, 800, 'Rechnung 123')
    document.drawString(30, 770, 'Gesamtbetrag: 123,45 EUR')
    document.save()
    checks['bounded_document_worker'] = classify_document(buffer.getvalue(), 'rechnung.pdf').kind == 'invoice'
    return checks
