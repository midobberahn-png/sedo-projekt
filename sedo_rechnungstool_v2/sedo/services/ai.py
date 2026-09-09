"""KI-Angebotsassistent (Anthropic Claude).

Diktat (läuft im Browser via Web Speech API) → Text → strukturierte
Angebotspositionen via Claude Tool-Use.

Bewusst enger Zuschnitt: Es werden ausschließlich Leistungsdaten an die Cloud
übertragen – niemals Kunden-, Objekt- oder Adressdaten. Die Spracherkennung
selbst läuft lokal im Browser, nicht über diese API. Reine Python-
Standardbibliothek (urllib), keine neuen Abhängigkeiten.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from ..models import AppSetting, ServiceCatalogItem

AI_API_BASE = 'https://api.anthropic.com/v1'
AI_API_VERSION = '2023-06-01'
AI_DEFAULT_TEXT_MODEL = 'claude-sonnet-5'
AI_MAX_TEXT_CHARS = 8000                   # Sicherheitslimit Diktatlänge
AI_MAX_POSITIONS = 40                      # Sicherheitslimit Positionsanzahl
AI_MAX_LONG_TEXT = 1200                    # Sicherheitslimit Leistungstextlänge
AI_TOOL_NAME = 'angebotspositionen_erfassen'
AI_REQUEST_TIMEOUT = 90                    # Sekunden für den Tool-Use-Request
AI_RETRY_WAIT_SECONDS = 1.0

_AI_PREIS_ARTEN = {'genannt', 'aufgeteilt', 'geschaetzt', 'offen'}


class AIError(Exception):
    """Fehler im KI-Assistenten – Meldung ist endnutzertauglich (deutsch)."""


def get_ai_config() -> dict:
    """Liest die KI-Konfiguration.

    Der API-Key kann alternativ als Umgebungsvariable ``SEDO_AI_API_KEY``
    (z. B. via .env) gesetzt werden – dann liegt er nicht in der Datenbank.
    Die Umgebungsvariable hat Vorrang vor dem AppSetting.
    """
    return {
        'api_key': os.environ.get('SEDO_AI_API_KEY', '').strip() or AppSetting.get('ai_api_key', '').strip(),
        'text_model': AppSetting.get('ai_text_model', '').strip() or AI_DEFAULT_TEXT_MODEL,
    }


def ai_is_configured() -> bool:
    return bool(get_ai_config()['api_key'])


def _ai_error_from_http(e: urllib.error.HTTPError, model: str = '') -> AIError:
    """Übersetzt einen Anthropic-HTTP-Fehler in eine verständliche deutsche Meldung.

    Fehlercodes gemäß Anthropic-API-Dokumentation (Stand 2026):
    400 invalid_request_error, 401 authentication_error, 402 billing_error,
    403 permission_error, 404 not_found_error, 413 request_too_large,
    429 rate_limit_error, 500 api_error, 529 overloaded_error.
    """
    code = e.code
    err_type = ''
    detail = ''
    try:
        body = json.loads(e.read().decode('utf-8', errors='replace'))
        inner = body.get('error') or {}
        err_type = inner.get('type', '') or ''
        detail = inner.get('message', '') or ''
    except (ValueError, AttributeError):
        pass

    if code == 401:
        return AIError('Der Claude-API-Key ist ungültig oder abgelaufen. Bitte unter Einstellungen → KI-Assistent prüfen.')
    if code == 402:
        return AIError('Es liegt ein Problem mit der Zahlungsmethode des Anthropic-Kontos vor. Bitte im Anthropic-Konto prüfen.')
    if code == 403:
        return AIError('Der API-Key hat keine Berechtigung für diese Anfrage. Bitte Workspace-Zugriff im Anthropic-Konto prüfen.')
    if code == 404 and model:
        return AIError(f'Das konfigurierte Modell „{model}" wurde nicht gefunden. Bitte Modellnamen unter Einstellungen → KI-Assistent prüfen.')
    if code == 413:
        return AIError('Das Diktat ist zu umfangreich für eine Anfrage. Bitte kürzer diktieren.')
    if code == 429:
        return AIError('Claude ist gerade ausgelastet (Rate-Limit erreicht). Bitte einen Moment warten und erneut versuchen.')
    if code == 529:
        return AIError('Die Claude-API ist aktuell überlastet. Bitte in ein paar Minuten erneut versuchen.')
    if code >= 500:
        return AIError('Claude meldet einen internen Serverfehler. Bitte in ein paar Minuten erneut versuchen.')
    kurz = (detail[:180] + '…') if len(detail) > 180 else detail
    if kurz:
        return AIError(f'Claude-Fehler (HTTP {code}, {err_type or "unbekannt"}). {kurz}')
    return AIError(f'Claude-Fehler (HTTP {code}). Bitte erneut versuchen.')


def _ai_request(req: urllib.request.Request, timeout: int, model: str = '') -> dict:
    """Führt einen HTTP-Request aus. Transiente Fehler (Netz/5xx/529) werden einmal wiederholt."""
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            err = _ai_error_from_http(e, model)
            if e.code in (500, 502, 503, 529) and attempt == 0:
                last_err = err
                time.sleep(AI_RETRY_WAIT_SECONDS)
                continue
            raise err from None
        except (urllib.error.URLError, TimeoutError, OSError):
            last_err = AIError('Keine Verbindung zu Claude möglich (Netzwerkfehler oder Zeitüberschreitung). Bitte Internetverbindung prüfen und erneut versuchen.')
            if attempt == 0:
                time.sleep(AI_RETRY_WAIT_SECONDS)
                continue
            raise last_err from None
        except json.JSONDecodeError:
            raise AIError('Die Antwort von Claude war nicht lesbar. Bitte erneut versuchen.') from None
    raise last_err or AIError('Unbekannter Fehler bei der KI-Anfrage.')


def _ai_post_json(path: str, payload: dict, api_key: str, timeout: int = 60, model: str = '') -> dict:
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        AI_API_BASE + path,
        data=data,
        headers={
            'x-api-key': api_key,
            'anthropic-version': AI_API_VERSION,
            'Content-Type': 'application/json',
        },
        method='POST',
    )
    return _ai_request(req, timeout=timeout, model=model)


# Input-Schema des Tools: vollständiger Angebotsentwurf
_AI_OFFER_INPUT_SCHEMA = {
    'type': 'object',
    'properties': {
        'positionen': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'titel': {'type': 'string', 'description': 'Kurzer, professioneller Positionstitel (deutsch, max. 10 Worte)'},
                    'leistungstext': {'type': 'string', 'description': 'Leistungsbeschreibung: 2–4 Sätze, was enthalten ist und wie ausgeführt wird (deutsch)'},
                    'menge': {'type': ['number', 'null'], 'description': 'Menge – bei Pauschalen 1'},
                    'einheit': {'type': ['string', 'null'], 'description': 'Einheit wie m², Stk., Std., Monat, pauschal'},
                    'einzelpreis': {'type': ['number', 'null'], 'description': 'Netto-Einzelpreis in Euro (Dezimalzahl mit Punkt); null nur wenn keine seriöse Schätzung möglich'},
                    'preis_art': {'type': 'string', 'enum': ['genannt', 'aufgeteilt', 'geschaetzt', 'offen'],
                                  'description': 'genannt = so diktiert; aufgeteilt = Anteil eines diktierten Gesamtpreises; geschaetzt = marktüblicher Vorschlag; offen = kein Preis möglich'},
                    'katalog_name': {'type': 'string', 'description': 'Generischer Name der Leistung für den Katalog (ohne Objekt-/Ortsbezug), z. B. „Unterhaltsreinigung Treppenhaus"'},
                    'katalog_neu': {'type': 'boolean', 'description': 'true wenn die Leistung NICHT im übergebenen Katalog steht'},
                    'artikel_nr': {'type': ['string', 'null']},
                },
                'required': ['titel', 'leistungstext', 'menge', 'einheit', 'einzelpreis', 'preis_art', 'katalog_name', 'katalog_neu', 'artikel_nr'],
            },
        },
        'annahmen': {
            'type': 'array', 'items': {'type': 'string'},
            'description': 'Jede getroffene Annahme als kurzer Satz (z. B. angenommener Turnus, geschätzte Fläche)',
        },
        'globale_hinweise': {
            'type': 'array', 'items': {'type': 'string'},
            'description': 'Kurze Hinweise auf Unklarheiten oder Prüfbedarf (deutsch)',
        },
    },
    'required': ['positionen', 'annahmen', 'globale_hinweise'],
}

_AI_SYSTEM_PROMPT_BASE = (
    'Du bist Angebots-Assistent eines Gebäudereinigungs- und Objektdienstleistungsunternehmens '
    '(Unterhaltsreinigung, Glas-/Grundreinigung, Beräumung/Entrümpelung, Hausmeisterdienste, Außenanlagen, Winterdienst).\n'
    'Aus einer groben mündlichen Ansage erstellst du einen VOLLSTÄNDIGEN Angebotsentwurf mit logisch '
    f'getrennten Einzelpositionen. Rufe dazu immer das Werkzeug "{AI_TOOL_NAME}" auf.\n'
    '\n'
    'Regeln:\n'
    '1. Zerlege die Ansage in branchenübliche Einzelpositionen (z. B. wird „Beräumung Reihenhaus mit Entsorgung" '
    'zu Positionen wie Beräumung/Demontage, Abtransport und fachgerechte Entsorgung – wenn sinnvoll).\n'
    '2. Jede Position: kurzer Titel + Leistungstext (2–4 Sätze: Leistungsumfang, Ausführung, ggf. Turnus). '
    'Professionelles Angebots-Deutsch, keine Floskeln, keine Übertreibungen, keine Garantiezusagen.\n'
    '3. Preise (preis_art immer korrekt setzen):\n'
    '   – Diktierter Preis für genau eine Leistung → übernehmen, preis_art = "genannt".\n'
    '   – Diktierter GESAMTpreis für mehrere Leistungen → plausibel auf die Positionen aufteilen; '
    'jede dieser Positionen preis_art = "aufgeteilt". Die Summe der Zeilenbeträge (Menge × Einzelpreis) '
    'muss EXAKT dem genannten Gesamtpreis entsprechen.\n'
    '   – Kein Preis genannt → marktüblichen Netto-Preis für Deutschland schätzen, preis_art = "geschaetzt".\n'
    '   – Nur wenn keine seriöse Schätzung möglich: einzelpreis = null, preis_art = "offen" plus Hinweis.\n'
    '4. Fehlende Angaben (Fläche, Turnus, Etagen …): sinnvolle branchenübliche Annahme treffen und JEDE '
    'Annahme einzeln in "annahmen" auflisten.\n'
    '5. Leistungskatalog: Unten steht der bestehende SEDO-Katalog. Passt eine Position inhaltlich zu einer '
    'Katalog-Leistung, verwende EXAKT deren Namen als katalog_name, setze katalog_neu = false und übernimm '
    'den Katalogtext als leistungstext (nur minimal an Menge/Turnus anpassen). Neue Leistungen bekommen einen '
    'generischen, wiederverwendbaren katalog_name OHNE Objekt-/Ortsbezug und katalog_neu = true.\n'
    '6. Einheiten: m², Stk., Std., Monat, pauschal. Mengen als Zahl; Pauschalen: menge = 1, einheit = pauschal.\n'
    '7. Datenschutz: Ignoriere Namen, Adressen und Telefonnummern aus dem Diktat vollständig – weder in '
    'Positionen noch in Katalognamen verwenden.\n'
    '8. KALKULATIONSANGABEN NICHT AUSWEISEN: Angaben zum internen Ansatz – Anzahl Arbeiter/Mitarbeiter/Mann, '
    'Arbeitstage, Arbeitsstunden, Stundensätze, Fahrzeuge, Container – dienen AUSSCHLIESSLICH deiner '
    'Preisfindung. Sie dürfen NIEMALS erscheinen: weder als eigene Position, noch im Titel, noch im '
    'Leistungstext, noch in den Annahmen oder Hinweisen. Beispiel: aus „Beräumung Reihenhaus, 3 Mann, '
    '2 Tage" wird EINE Position „Beräumung Reihenhaus" (menge = 1, einheit = pauschal) mit einem daraus '
    'kalkulierten Pauschalpreis – ohne jede Erwähnung von Mann, Tagen oder Stunden. Ausnahme: Der Kunde soll '
    'ausdrücklich nach Stunden abgerechnet werden (z. B. „auf Stundenbasis", „nach Aufwand abrechnen") – '
    'nur dann ist einheit = Std. zulässig.\n'
    '9. Prüfe das Ergebnis auf Logik (Doppelungen, Widersprüche, unrealistische Werte) und vermerke '
    'Auffälligkeiten in "globale_hinweise".'
)


def _service_catalog_prompt_block(limit: int = 60, max_chars: int = 6000) -> str:
    """Baut den Katalog-Abschnitt für den System-Prompt (Name + Text der Leistungen)."""
    lines = []
    used = 0
    for item in ServiceCatalogItem.objects.all()[:limit]:
        text = re.sub(r'\s+', ' ', item.long_text or '').strip()
        line = f'- {item.name}' + (f' ({item.default_unit})' if item.default_unit else '') + (f': {text}' if text else '')
        if used + len(line) > max_chars:
            lines.append('- … (weitere Einträge gekürzt)')
            break
        lines.append(line)
        used += len(line)
    if not lines:
        return '\n\nBestehender SEDO-Leistungskatalog: (noch leer – alle Leistungen sind neu, katalog_neu = true)'
    return '\n\nBestehender SEDO-Leistungskatalog:\n' + '\n'.join(lines)


def _ai_to_decimal(value) -> Decimal | None:
    """Wandelt LLM-Werte (Zahl oder String, auch deutsches Format) in Decimal um."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        try:
            return Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None
    if isinstance(value, str):
        s = value.strip().replace('€', '').replace(' ', '')
        if not s:
            return None
        if '.' in s and ',' in s:      # deutsches Format 1.234,56
            s = s.replace('.', '').replace(',', '.')
        elif ',' in s:                 # 0,45
            s = s.replace(',', '.')
        try:
            return Decimal(s).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None
    return None


def _ai_clean_str(value, max_len: int) -> str:
    if not isinstance(value, str):
        return ''
    s = re.sub(r'\s+', ' ', value).strip()
    if len(s) > max_len:
        s = s[:max_len - 1].rstrip() + '…'
    return s


_AI_UNIT_MAP = {
    'm2': 'm²', 'qm': 'm²', 'quadratmeter': 'm²', 'm^2': 'm²',
    'stunde': 'Std.', 'stunden': 'Std.', 'std': 'Std.', 'std.': 'Std.', 'h': 'Std.',
    'stück': 'Stk.', 'stueck': 'Stk.', 'stk': 'Stk.', 'stk.': 'Stk.',
    'pauschal': 'pauschal', 'psch': 'pauschal', 'psch.': 'pauschal', 'pauschale': 'pauschal',
    'monat': 'Monat', 'monate': 'Monat', 'monatlich': 'Monat',
}


def _ai_normalize_unit(value) -> str:
    s = _ai_clean_str(value, 40)
    if not s:
        return ''
    return _AI_UNIT_MAP.get(s.lower(), s)


def _dedup(seq):
    """Reihenfolge erhaltend deduplizieren."""
    return list(dict.fromkeys(seq))


def normalize_ai_positions(raw: dict) -> dict:
    """Validiert und normalisiert den KI-Angebotsentwurf zu formularfertigen Positionen.

    Rückgabe: {'positionen': [...], 'hinweise': [...], 'annahmen': [...]}
    Zahlen als Strings mit Punkt-Dezimaltrennzeichen; fehlender Preis = leerer String.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get('positionen'), list):
        raise AIError('Die KI-Antwort hatte ein unerwartetes Format. Bitte erneut versuchen.')

    hinweise: list[str] = []
    annahmen: list[str] = []
    positionen: list[dict] = []
    aufgeteilt_indices: list[int] = []
    aufgeteilt_summe = Decimal('0.00')

    for a in (raw.get('annahmen') or [])[:12]:
        a = _ai_clean_str(a, 220)
        if a:
            annahmen.append(a)
    for h in (raw.get('globale_hinweise') or [])[:10]:
        h = _ai_clean_str(h, 220)
        if h:
            hinweise.append(h)

    for idx, item in enumerate(raw['positionen'][:AI_MAX_POSITIONS], start=1):
        if not isinstance(item, dict):
            continue
        titel = _ai_clean_str(item.get('titel') or item.get('bezeichnung'), 250)
        if not titel:
            continue
        leistungstext = ''
        if isinstance(item.get('leistungstext'), str):
            leistungstext = item['leistungstext'].strip()
            if len(leistungstext) > AI_MAX_LONG_TEXT:
                leistungstext = leistungstext[:AI_MAX_LONG_TEXT - 1].rstrip() + '…'

        einheit = _ai_normalize_unit(item.get('einheit'))

        menge = _ai_to_decimal(item.get('menge'))
        if menge is None or menge <= 0:
            if item.get('menge') not in (None, ''):
                hinweise.append(f'Position {idx}: Menge war unklar – als Pauschale (1) übernommen, bitte prüfen.')
            menge = Decimal('1.00')
            if not einheit:
                einheit = 'pauschal'
        elif menge > Decimal('100000'):
            hinweise.append(f'Position {idx}: Ungewöhnlich große Menge ({menge}) – bitte prüfen.')

        if not einheit:
            einheit = 'Stk.'

        preis_art = item.get('preis_art') if item.get('preis_art') in _AI_PREIS_ARTEN else None
        preis = _ai_to_decimal(item.get('einzelpreis'))
        preis_str = ''
        if preis is None:
            preis_art = 'offen'
            hinweise.append(f'Position {idx} („{titel[:40]}"): Preis offen – bitte ergänzen.')
        else:
            if preis_art is None:
                preis_art = 'genannt'
            if preis < 0:
                hinweise.append(f'Position {idx}: Negativer Preis ({preis} €) – bitte prüfen.')
            elif preis > Decimal('100000'):
                hinweise.append(f'Position {idx}: Ungewöhnlich hoher Einzelpreis ({preis} €) – bitte prüfen.')
            if einheit == 'm²' and preis > 0 and (preis < Decimal('0.05') or preis > Decimal('20')):
                hinweise.append(f'Position {idx}: {preis} € je m² wirkt ungewöhnlich – bitte prüfen.')
            preis_str = str(preis)
            if preis_art == 'geschaetzt':
                hinweise.append(f'Position {idx} („{titel[:40]}"): Preis {preis} € ist ein KI-Vorschlag (marktüblich geschätzt) – bitte prüfen.')
            elif preis_art == 'aufgeteilt':
                aufgeteilt_indices.append(idx)
                aufgeteilt_summe += (menge * preis).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)

        positionen.append({
            'bezeichnung': titel,
            'leistungstext': leistungstext,
            'menge': str(menge),
            'einheit': einheit,
            'einzelpreis': preis_str,
            'preis_art': preis_art,
            'katalog_name': _ai_clean_str(item.get('katalog_name'), 120),
            'katalog_neu': bool(item.get('katalog_neu')),
            'artikel_nr': _ai_clean_str(item.get('artikel_nr'), 80),
        })

    if aufgeteilt_indices:
        pos_liste = ', '.join(str(i) for i in aufgeteilt_indices)
        hinweise.append(f'Positionen {pos_liste}: Genannter Gesamtpreis wurde aufgeteilt (Zeilensumme {aufgeteilt_summe} € netto) – Aufteilung bitte prüfen.')

    if not positionen and not hinweise:
        hinweise.append('Es konnten keine Positionen erzeugt werden. Bitte die gewünschte Leistung kurz beschreiben.')

    return {
        'positionen': positionen,
        'hinweise': _dedup(hinweise)[:14],
        'annahmen': _dedup(annahmen)[:12],
    }


def apply_and_learn_service_catalog(positionen: list[dict]) -> str:
    """Wendet vorhandene Katalogtexte an (Konsistenz) und legt neue Leistungen an.

    Selbstlernender Katalog: Bekannte Leistung → gespeicherter Text ersetzt den
    generierten (damit jedes Angebot gleich klingt). Neue Leistung → wird mit dem
    generierten Text in den Katalog übernommen (created_by_ai=True).

    Hinweis: Diese Funktion verändert die übergebenen Positionen (leistungstext)
    absichtlich in-place.
    Rückgabe: kurze Info-Meldung für die Oberfläche ('' wenn nichts passiert ist).
    """
    wiederverwendet = 0
    neu_angelegt: list[str] = []
    for pos in positionen:
        name = (pos.get('katalog_name') or '').strip()
        if not name:
            continue
        existing = ServiceCatalogItem.objects.filter(name__iexact=name).first()
        if existing:
            if existing.long_text.strip():
                if pos.get('leistungstext') != existing.long_text:
                    pos['leistungstext'] = existing.long_text
                wiederverwendet += 1
            elif pos.get('leistungstext'):
                existing.long_text = pos['leistungstext']
                if not existing.default_unit and pos.get('einheit'):
                    existing.default_unit = pos['einheit'][:40]
                existing.save(update_fields=['long_text', 'default_unit', 'updated_at'])
        elif pos.get('leistungstext'):
            ServiceCatalogItem.objects.create(
                name=name[:120],
                long_text=pos['leistungstext'],
                default_unit=(pos.get('einheit') or '')[:40],
                created_by_ai=True,
            )
            neu_angelegt.append(name)
    teile = []
    if wiederverwendet:
        teile.append(f'{wiederverwendet} Leistungstext(e) aus dem Katalog übernommen')
    if neu_angelegt:
        teile.append(f'{len(neu_angelegt)} neue Leistung(en) im Katalog angelegt: {", ".join(neu_angelegt[:5])}')
    return ' · '.join(teile)


def _ai_generate_via_tool_use(text: str, cfg: dict) -> dict:
    """Ruft die Anthropic Messages API mit erzwungenem Tool-Use auf."""
    payload = {
        'model': cfg['text_model'],
        'max_tokens': 8000,
        'system': _AI_SYSTEM_PROMPT_BASE + _service_catalog_prompt_block(),
        'messages': [{'role': 'user', 'content': f'Ansage:\n{text}'}],
        'tools': [{
            'name': AI_TOOL_NAME,
            'description': 'Erfasst den aus der Ansage erzeugten vollständigen Angebotsentwurf strukturiert.',
            'input_schema': _AI_OFFER_INPUT_SCHEMA,
        }],
        'tool_choice': {'type': 'tool', 'name': AI_TOOL_NAME},
    }
    data = _ai_post_json('/messages', payload, cfg['api_key'], timeout=AI_REQUEST_TIMEOUT, model=cfg['text_model'])

    if data.get('stop_reason') == 'refusal':
        raise AIError('Claude hat die Anfrage abgelehnt. Bitte die Ansage prüfen und erneut versuchen.')

    content = data.get('content') or []
    for block in content:
        if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') == AI_TOOL_NAME:
            tool_input = block.get('input')
            if isinstance(tool_input, dict):
                return tool_input
            raise AIError('Die KI-Antwort hatte ein unerwartetes Format. Bitte erneut versuchen.')
    raise AIError('Claude hat keine strukturierte Antwort geliefert. Bitte erneut versuchen.')


def generate_offer_from_text(text: str) -> dict:
    """Hauptfunktion: grobe Ansage → vollständiger, normalisierter Angebotsentwurf.

    Rückgabe: {'positionen', 'hinweise', 'annahmen', 'katalog_info'}
    """
    cfg = get_ai_config()
    if not cfg['api_key']:
        raise AIError('Kein Claude-API-Key hinterlegt. Bitte unter Einstellungen → KI-Assistent eintragen.')
    text = (text or '').strip()
    if len(text) < 5:
        raise AIError('Bitte zuerst diktieren oder einen Text eingeben.')
    gekuerzt = False
    if len(text) > AI_MAX_TEXT_CHARS:
        text = text[:AI_MAX_TEXT_CHARS]
        gekuerzt = True

    raw = _ai_generate_via_tool_use(text, cfg)
    result = normalize_ai_positions(raw)
    result['katalog_info'] = apply_and_learn_service_catalog(result['positionen'])
    if gekuerzt:
        result['hinweise'].insert(0, 'Die Ansage war sehr lang und wurde gekürzt – bitte Vollständigkeit prüfen.')
    return result


# Rückwärtskompatibler Alias (Tests/ältere Aufrufer)
def extract_offer_positions_from_text(text: str) -> dict:
    return generate_offer_from_text(text)


# ═══════════════════════════════════════════════════════════════════════════
# Magic-Tone (v3.21): loses/emotionales Diktat → professioneller Geschäftstext.
# (a) Leistungstexte einzelner Positionen veredeln
# (b) Begleitschreiben für den Rechnungsversand erzeugen
# Datenschutz unverändert: nur Leistungsdaten, neutrale Anrede, keine Namen.
# ═══════════════════════════════════════════════════════════════════════════

AI_POLISH_MAX_CHARS = 2500                 # Sicherheitslimit Eingabetext
_AI_TEXT_TOOL = 'text_ausgeben'
_AI_TEXT_SCHEMA = {
    'type': 'object',
    'properties': {'text': {'type': 'string', 'description': 'Der fertige deutsche Text.'}},
    'required': ['text'],
}

_AI_POLISH_SYSTEM = (
    'Du formulierst für ein Gebäudereinigungs- und Objektdienstleistungsunternehmen '
    'lose, umgangssprachliche oder emotionale Ansagen in eine professionelle '
    'Leistungsbeschreibung für Angebote/Rechnungen um (2–4 Sätze, sachliches '
    f'Angebots-Deutsch). Rufe immer das Werkzeug "{_AI_TEXT_TOOL}" auf.\n'
    'Regeln:\n'
    '1. Inhaltlich nichts erfinden – nur das Genannte professionell fassen.\n'
    '2. Keine Namen, Adressen, Telefonnummern oder Schuldzuweisungen übernehmen; '
    'Personen- und Firmennamen ersatzlos weglassen.\n'
    '3. Kalkulationsangaben (Mitarbeiterzahl, Stunden, Stundensätze) nicht ausweisen, '
    'außer es geht ausdrücklich um Abrechnung nach Stunden.\n'
    '4. Keine Floskeln, keine Übertreibungen, keine Garantie- oder Rechtszusagen.\n'
    '5. Emotionale oder verärgerte Formulierungen neutral und kundenfreundlich fassen.'
)

_AI_COVER_LETTER_SYSTEM = (
    'Du schreibst für ein Gebäudereinigungs- und Objektdienstleistungsunternehmen den '
    'Textkörper einer kurzen, freundlichen Begleit-E-Mail zu einer Rechnung. '
    f'Rufe immer das Werkzeug "{_AI_TEXT_TOOL}" auf.\n'
    'Regeln:\n'
    '1. Anrede immer neutral: "Sehr geehrte Damen und Herren,". Keine Namen verwenden – '
    'auch nicht, wenn im Hinweis welche vorkommen.\n'
    '2. Kurz und professionell (max. 120 Wörter), keine Betreffzeile, keine Platzhalter.\n'
    '3. Rechnungsnummer, Betrag und Fälligkeitsdatum aus den Eckdaten korrekt nennen.\n'
    '4. Grußformel am Ende: "Mit freundlichen Grüßen" + Firmenname '
    '"SEDO Objektdienstleistungen GmbH".\n'
    '5. Optionale Hinweise des Nutzers (Anlass, Ton, Besonderheiten) einarbeiten, '
    'dabei Regel 1 und 4 beibehalten. Nichts erfinden.'
)


def _ai_single_text(system_prompt: str, user_content: str) -> str:
    """Gemeinsamer Unterbau: erzwungener Tool-Use, Rückgabe ist ein einzelner Text."""
    cfg = get_ai_config()
    if not cfg['api_key']:
        raise AIError('Kein Claude-API-Key hinterlegt. Bitte unter Einstellungen → KI-Assistent eintragen.')
    payload = {
        'model': cfg['text_model'],
        'max_tokens': 1000,
        'system': system_prompt,
        'messages': [{'role': 'user', 'content': user_content}],
        'tools': [{
            'name': _AI_TEXT_TOOL,
            'description': 'Gibt den fertigen deutschen Text strukturiert zurück.',
            'input_schema': _AI_TEXT_SCHEMA,
        }],
        'tool_choice': {'type': 'tool', 'name': _AI_TEXT_TOOL},
    }
    data = _ai_post_json('/messages', payload, cfg['api_key'], timeout=60, model=cfg['text_model'])
    if data.get('stop_reason') == 'refusal':
        raise AIError('Claude hat die Anfrage abgelehnt. Bitte den Text prüfen und erneut versuchen.')
    for block in (data.get('content') or []):
        if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') == _AI_TEXT_TOOL:
            tool_input = block.get('input')
            if isinstance(tool_input, dict) and isinstance(tool_input.get('text'), str):
                text = tool_input['text'].strip()
                if text:
                    return text
    raise AIError('Claude hat keinen verwendbaren Text geliefert. Bitte erneut versuchen.')


def polish_service_text(text: str) -> str:
    """Veredelt eine lose Ansage zu einer professionellen Leistungsbeschreibung."""
    text = (text or '').strip()
    if len(text) < 3:
        raise AIError('Bitte zuerst einen Text eingeben oder diktieren.')
    if len(text) > AI_POLISH_MAX_CHARS:
        text = text[:AI_POLISH_MAX_CHARS]
    return _ai_single_text(_AI_POLISH_SYSTEM, f'Ansage:\n{text}')


_AI_REVIEW_TOOL = 'befunde_melden'
_AI_REVIEW_SCHEMA = {
    'type': 'object',
    'properties': {
        'befunde': {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'position': {'type': ['integer', 'null'],
                                 'description': 'Betroffene Positionsnummer (1-basiert) oder null bei dokumentweiten Befunden.'},
                    'schwere': {'type': 'string', 'enum': ['warnung', 'hinweis']},
                    'text': {'type': 'string', 'description': 'Kurzer deutscher Befund mit konkretem Prüfvorschlag.'},
                },
                'required': ['position', 'schwere', 'text'],
            },
        },
    },
    'required': ['befunde'],
}

_AI_REVIEW_SYSTEM = (
    'Du prüfst Angebots-/Rechnungspositionen eines Gebäudereinigungs- und '
    'Objektdienstleistungsunternehmens auf kaufmännische Plausibilität. '
    f'Rufe immer das Werkzeug "{_AI_REVIEW_TOOL}" auf.\n'
    'Prüfe AUSSCHLIESSLICH:\n'
    '1. Inhaltliche Dubletten oder Überschneidungen zwischen Positionen.\n'
    '2. Unplausible Kombinationen aus Menge, Einheit und Einzelpreis '
    '(marktüblich Deutschland, z. B. €/m² weit außerhalb üblicher Bandbreiten, '
    'Stundensätze unter Mindestlohnniveau, offensichtliche Tippfehler).\n'
    '3. Fehlende typische Begleitpositionen – NUR wenn der unten stehende Katalog '
    'eine offensichtlich zugehörige Leistung enthält (z. B. Beräumung ohne Entsorgung).\n'
    '4. Widersprüchliche oder unpassende Einheiten.\n'
    'Regeln: Keine Stil- oder Formulierungsanmerkungen. Keine erfundenen Marktdaten '
    'oder Statistiken. Maximal 6 Befunde, die wichtigsten zuerst. '
    'Wenn nichts auffällt: leere Liste zurückgeben.'
)

AI_REVIEW_MAX_FINDINGS = 6


def ai_review_positions(positionen: list[dict]) -> list[dict]:
    """KI-Plausibilitätsprüfung der Positionen (nur Leistungsdaten).

    ``positionen``: Liste aus {'bezeichnung', 'menge', 'einheit', 'einzelpreis'}.
    Rückgabe wie ``check_positions_local``: Liste aus
    {'position': int|None, 'schwere': 'warnung'|'hinweis', 'text': str}.
    """
    cfg = get_ai_config()
    if not cfg['api_key']:
        raise AIError('Kein Claude-API-Key hinterlegt. Bitte unter Einstellungen → KI-Assistent eintragen.')
    lines = []
    for idx, pos in enumerate(positionen[:AI_MAX_POSITIONS], start=1):
        desc = _ai_clean_str(pos.get('bezeichnung'), 200)
        if not desc:
            continue
        menge = _ai_clean_str(pos.get('menge'), 20) or '1'
        einheit = _ai_clean_str(pos.get('einheit'), 20) or 'Stk.'
        preis = _ai_clean_str(pos.get('einzelpreis'), 20) or 'offen'
        lines.append(f'{idx}. {desc} – {menge} {einheit} × {preis} €')
    if not lines:
        raise AIError('Keine prüfbaren Positionen vorhanden.')

    payload = {
        'model': cfg['text_model'],
        'max_tokens': 1500,
        'system': _AI_REVIEW_SYSTEM + _service_catalog_prompt_block(),
        'messages': [{'role': 'user', 'content': 'Positionen:\n' + '\n'.join(lines)}],
        'tools': [{
            'name': _AI_REVIEW_TOOL,
            'description': 'Meldet die gefundenen Plausibilitäts-Befunde strukturiert.',
            'input_schema': _AI_REVIEW_SCHEMA,
        }],
        'tool_choice': {'type': 'tool', 'name': _AI_REVIEW_TOOL},
    }
    data = _ai_post_json('/messages', payload, cfg['api_key'], timeout=60, model=cfg['text_model'])
    if data.get('stop_reason') == 'refusal':
        raise AIError('Claude hat die Anfrage abgelehnt. Bitte erneut versuchen.')
    for block in (data.get('content') or []):
        if isinstance(block, dict) and block.get('type') == 'tool_use' and block.get('name') == _AI_REVIEW_TOOL:
            raw = block.get('input')
            if not isinstance(raw, dict) or not isinstance(raw.get('befunde'), list):
                raise AIError('Die KI-Antwort hatte ein unerwartetes Format. Bitte erneut versuchen.')
            findings = []
            for item in raw['befunde'][:AI_REVIEW_MAX_FINDINGS]:
                if not isinstance(item, dict):
                    continue
                text = _ai_clean_str(item.get('text'), 300)
                if not text:
                    continue
                pos_no = item.get('position')
                findings.append({
                    'position': pos_no if isinstance(pos_no, int) and 1 <= pos_no <= len(lines) else None,
                    'schwere': item.get('schwere') if item.get('schwere') in ('warnung', 'hinweis') else 'hinweis',
                    'text': text,
                })
            return findings
    raise AIError('Claude hat keine strukturierte Antwort geliefert. Bitte erneut versuchen.')


def generate_cover_letter_text(context_lines: list[str], hint: str = '') -> str:
    """Erzeugt den Begleit-E-Mail-Text zu einer Rechnung.

    ``context_lines`` enthält AUSSCHLIESSLICH Leistungs-/Rechnungseckdaten
    (Nummer, Betrag, Fälligkeit, Positionen) – niemals Kunden- oder Objektdaten.
    """
    content = 'Eckdaten der Rechnung:\n' + '\n'.join(context_lines)
    hint = (hint or '').strip()
    if hint:
        if len(hint) > 500:
            hint = hint[:500]
        content += f'\n\nHinweis des Nutzers: {hint}'
    return _ai_single_text(_AI_COVER_LETTER_SYSTEM, content)
