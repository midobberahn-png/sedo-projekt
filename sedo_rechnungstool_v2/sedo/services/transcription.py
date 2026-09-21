"""Lokale Spracherkennung (Diktat → Text) mit faster-whisper.

Datenschutz: Das Audio wird ausschließlich an den EIGENEN Server geschickt und
dort lokal transkribiert – es verlässt nie den Rechner (kein Cloud-Upload).
Nach der Transkription wird die Audiodatei sofort gelöscht.

faster-whisper ist eine optionale Abhängigkeit: Ist sie nicht installiert,
meldet ``stt_status()`` das sauber. Die Oberfläche bietet dann Texteingabe;
es gibt keinen Wechsel zu externer Browser-Spracherkennung.

Beim ersten Aufruf lädt faster-whisper das Modell einmalig herunter
(Standard „small“, ~460 MB) und cached es lokal; danach läuft alles offline.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..models import AppSetting

# Modellgröße: tiny/base/small/medium – „small“ ist der beste Kompromiss aus
# Qualität (Deutsch, Fachbegriffe, Zahlen) und Geschwindigkeit auf CPU.
STT_DEFAULT_MODEL = 'small'
# Obergrenze für hochgeladenes Audio (Diktate sind kurz; 25 MB ≈ >10 Minuten Opus).
STT_MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Fachvokabular als initial_prompt: zwingt Whisper, Zahlen und Branchenbegriffe
# direkt korrekt zu schreiben (z. B. „19 %“ statt „neunzehn Prozent“).
STT_INITIAL_PROMPT = (
    'Diktat für ein Angebot der Gebäudereinigung: Unterhaltsreinigung, '
    'Grundreinigung, Glasreinigung, Treppenhaus, Beräumung, Entrümpelung, '
    'Winterdienst, Hausmeisterdienst, Außenanlagen. Netto, Brutto, MwSt 19 %, '
    'pauschal 350 €, 120 m², 3 Stunden, monatlich, Position, Artikelnummer.'
)

# Lazy geladenes Modell (einmal pro Prozess; Laden dauert einige Sekunden).
_model = None
_model_name: str | None = None


class TranscriptionUnavailable(Exception):
    """faster-whisper ist nicht installiert oder das Modell lädt nicht."""


def stt_model_name() -> str:
    """Konfigurierte Modellgröße (AppSetting ``stt_model``, Standard „small“)."""
    return (AppSetting.get('stt_model', '') or '').strip() or STT_DEFAULT_MODEL


def stt_status() -> dict:
    """Verfügbarkeit der lokalen Spracherkennung, ohne das Modell zu laden."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return {
            'available': False,
            'reason': 'faster-whisper ist nicht installiert (pip install faster-whisper).',
        }
    return {'available': True, 'model': stt_model_name()}


def _get_model():
    """Lädt das Whisper-Modell einmalig (CPU, int8-quantisiert)."""
    global _model, _model_name
    name = stt_model_name()
    if _model is not None and _model_name == name:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise TranscriptionUnavailable(
            'Lokale Spracherkennung nicht verfügbar: faster-whisper ist nicht installiert.'
        ) from exc
    try:
        _model = WhisperModel(name, device='auto', compute_type='int8')
        _model_name = name
    except Exception as exc:  # Modell-Download/Initialisierung fehlgeschlagen
        raise TranscriptionUnavailable(
            f'Das Whisper-Modell „{name}“ konnte nicht geladen werden: {exc}'
        ) from exc
    return _model


def transcribe_audio(audio_bytes: bytes, suffix: str = '.webm') -> str:
    """Transkribiert ein Audio-Diktat (Bytes) lokal zu deutschem Text.

    Das Audio wird nur in eine temporäre Datei geschrieben und danach gelöscht.
    Wirft ``TranscriptionUnavailable``, wenn faster-whisper fehlt, oder
    ``ValueError`` bei leerem/zu großem Audio.
    """
    if not audio_bytes:
        raise ValueError('Es wurde kein Audio übertragen.')
    if len(audio_bytes) > STT_MAX_AUDIO_BYTES:
        raise ValueError('Die Aufnahme ist zu lang. Bitte in kürzeren Abschnitten diktieren.')

    model = _get_model()
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        tmp.write(audio_bytes)
        tmp.close()
        segments, _info = model.transcribe(
            tmp.name,
            language='de',
            initial_prompt=STT_INITIAL_PROMPT,
            vad_filter=True,      # Stille/Atmer herausfiltern
            beam_size=5,
        )
        text = ' '.join(seg.text.strip() for seg in segments).strip()
    finally:
        Path(tmp.name).unlink(missing_ok=True)
    return text
