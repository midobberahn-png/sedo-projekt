"""Local Bonsai transport. No cloud credentials, proxies or redirects.

The model is discovered from the running server, since GGUF model IDs can be
machine-specific paths. All four SEDO assistants share this transport.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request

from ..models import AppSetting

AI_DEFAULT_BASE_URL = 'http://127.0.0.1:8080/v1'
AI_DEFAULT_TEXT_MODEL = ''  # Discover the single loaded model.
AI_REQUEST_TIMEOUT = 300
AI_MAX_RESPONSE_BYTES = 512 * 1024


class AIError(Exception):
    """End-user-safe message; never echo model/server output or request data."""


def validate_local_base_url(value: str) -> str:
    """Only literal loopback targets; localhost is pinned to 127.0.0.1."""
    value = value.strip()
    try:
        if not value or any(ord(c) < 33 or ord(c) > 126 for c in value):
            raise ValueError
        url = urllib.parse.urlsplit(value)
        if (url.scheme not in ('http', 'https') or
                url.hostname not in ('localhost', '127.0.0.1', '::1') or
                url.username is not None or url.password is not None or
                url.query or url.fragment or url.path.rstrip('/') not in ('', '/v1')):
            raise ValueError
        port = url.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        host = '[::1]' if url.hostname == '::1' else '127.0.0.1'
        return f'{url.scheme}://{host}' + (f':{port}' if port is not None else '') + '/v1'
    except ValueError:
        raise AIError('Bitte eine lokale Adresse wie http://127.0.0.1:8080/v1 verwenden. '
                      'Erlaubt sind nur localhost, 127.0.0.1 oder [::1] ohne Zugangsdaten.') from None


def validate_local_model(value: str) -> str:
    value = value.strip()
    if len(value) > 512 or any(ord(c) < 32 for c in value):
        raise AIError('Die Modellkennung ist ungültig. Leer lassen für automatische Erkennung.')
    return value


def get_ai_config() -> dict:
    return {
        'enabled': AppSetting.get('ai_local_enabled', '1') == '1',
        'autostart': AppSetting.get('ai_local_autostart', '1') == '1',
        'runtime_dir': AppSetting.get('ai_local_runtime_dir', '').strip(),
        'base_url': os.environ.get('SEDO_AI_LOCAL_BASE_URL', '').strip() or
                    AppSetting.get('ai_local_base_url', '').strip() or AI_DEFAULT_BASE_URL,
        'text_model': os.environ.get('SEDO_AI_LOCAL_MODEL', '').strip() or
                      AppSetting.get('ai_local_model', '').strip(),
        'endpoint_from_env': bool(os.environ.get('SEDO_AI_LOCAL_BASE_URL', '').strip()),
        'model_from_env': bool(os.environ.get('SEDO_AI_LOCAL_MODEL', '').strip()),
    }


def ai_is_configured() -> bool:
    # Availability is checked explicitly, never during a page render.
    return get_ai_config()['enabled']


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _reject_constant(value):
    raise ValueError('Non-finite JSON number')


def _local_request(cfg: dict, path: str, payload=None, timeout=5) -> dict:
    base = validate_local_base_url(cfg['base_url'])
    req = urllib.request.Request(
        base + path,
        data=json.dumps(payload, allow_nan=False).encode('utf-8') if payload is not None else None,
        headers={'Content-Type': 'application/json', 'Accept': 'application/json'},
    )
    # System HTTP(S)_PROXY variables must never forward business data externally.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(req, timeout=timeout) as response:
            body = response.read(AI_MAX_RESPONSE_BYTES + 1)
        if len(body) > AI_MAX_RESPONSE_BYTES:
            raise AIError('Die Antwort von Bonsai ist zu groß. Bitte die Anfrage verkürzen.')
        result = json.loads(body, parse_constant=_reject_constant)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except urllib.error.HTTPError as exc:
        exc.close()
        if 300 <= exc.code < 400:
            raise AIError('Bonsai hat eine Weiterleitung angefordert. Aus Datenschutzgründen '
                          'wurde sie blockiert. Bitte die lokale Adresse prüfen.') from None
        if exc.code in (429, 503):
            raise AIError('Bonsai lädt noch oder ist ausgelastet. Bitte später erneut versuchen.') from None
        if exc.code in (401, 403):
            raise AIError('Der lokale Modellserver verlangt eine Zugangsberechtigung. '
                          'Bitte seine lokale Konfiguration prüfen.') from None
        raise AIError(f'Bonsai konnte die Anfrage nicht bearbeiten (HTTP {exc.code}). '
                      'Bitte Modell und Server prüfen oder die Anfrage verkürzen.') from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise AIError('Bonsai ist nicht erreichbar oder die Antwort dauert zu lange. '
                      'Bitte den lokalen Modellserver starten bzw. die Anfrage verkürzen. '
                      'Es wurde kein Cloud-Dienst verwendet.') from None
    except (ValueError, UnicodeError, RecursionError):
        raise AIError('Die Antwort von Bonsai war nicht lesbar. Bitte erneut versuchen.') from None


def check_local_model(cfg: dict | None = None) -> str:
    cfg = cfg if cfg is not None else get_ai_config()
    if not cfg['enabled']:
        raise AIError('Der lokale KI-Assistent ist deaktiviert. Bitte in den Einstellungen aktivieren.')
    selected = validate_local_model(cfg['text_model'])
    data = _local_request(cfg, '/models').get('data')
    if not isinstance(data, list):
        raise AIError('Bonsai hat keine lesbare Modellliste geliefert.')
    models = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get('id'), str):
            model = validate_local_model(item['id'])
            if model and model not in models:
                models.append(model)
    if selected and selected in models:
        return selected
    if selected:
        raise AIError('Das eingestellte Modell ist nicht geladen. Bitte die Modellkennung '
                      'prüfen oder für automatische Erkennung leeren.')
    if len(models) != 1:
        raise AIError('Kein eindeutiges lokales Modell gefunden. Bitte genau ein Modell '
                      'laden oder seine Kennung in den KI-Einstellungen auswählen.')
    return models[0]


def _matches_schema(value, schema: dict) -> bool:
    """Validate the small JSON-schema subset used by SEDO, independently of the LLM.

    Constrained decoding alone is not a trust boundary. Reject missing fields,
    wrong types (including bool as a number), invalid enums and non-finite values.
    """
    types = schema.get('type', [])
    types = [types] if isinstance(types, str) else types
    actual = ('null' if value is None else 'boolean' if isinstance(value, bool) else
              'string' if isinstance(value, str) else 'object' if isinstance(value, dict) else
              'array' if isinstance(value, list) else 'integer' if isinstance(value, int) else
              'number' if isinstance(value, float) and math.isfinite(value) else 'invalid')
    if actual not in types and not (actual == 'integer' and 'number' in types):
        return False
    if 'enum' in schema and value not in schema['enum']:
        return False
    if actual == 'object':
        props = schema.get('properties', {})
        return (all(key in value for key in schema.get('required', [])) and
                all(key in props and _matches_schema(item, props[key]) for key, item in value.items()))
    if actual == 'array':
        return all(_matches_schema(item, schema['items']) for item in value)
    return True


def generate_structured(system: str, user: str, schema: dict, *, max_tokens=1500, cfg=None) -> dict:
    cfg = cfg if cfg is not None else get_ai_config()
    model = check_local_model(cfg)
    payload = {
        'model': model,
        'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        'max_tokens': max_tokens,
        'temperature': 0.2,
        'stream': False,
        'reasoning_effort': 'none',
        'chat_template_kwargs': {'enable_thinking': False},
        'response_format': {'type': 'json_object', 'schema': schema},
    }
    data = _local_request(cfg, '/chat/completions', payload, timeout=AI_REQUEST_TIMEOUT)
    try:
        choice = data['choices'][0]
        if choice.get('finish_reason') == 'length':
            raise AIError('Der Bonsai-Entwurf ist unvollständig. Bitte die Anfrage in kleinere '
                          'Abschnitte teilen. Es wurden keine Teilpositionen übernommen.')
        if choice.get('finish_reason') != 'stop':
            raise ValueError
        raw = json.loads(choice['message']['content'], parse_constant=_reject_constant)
        if not _matches_schema(raw, schema):
            raise ValueError
        return raw
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        raise AIError('Bonsai hat keine vollständige, passende Antwort geliefert. '
                      'Bitte die Eingabe prüfen und erneut versuchen.') from None
