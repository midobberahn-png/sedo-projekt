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

import threading
from pathlib import Path

from ..models import AppSetting

# Unterstützte Modellgrößen: tiny/base/small. „small“ bleibt der Standard.
# Größere Modelle überschreiten das feste 1536-MiB-Arbeitsspeicherbudget des
# isolierten Workers und werden bereits vor einem Download zurückgewiesen.
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

# One recording/model download at a time; expensive parsing happens in a child.
_TRANSCRIPTION_SLOT = threading.BoundedSemaphore(1)
# Verified 2026-10-01 against the official model metadata endpoints:
# https://huggingface.co/api/models/Systran/faster-whisper-{name}
# Pin the immutable revision, not a mutable repository branch or model alias.
_MODEL_REVISIONS = {
    'tiny': 'd90ca5fe260221311c53c58e660288d3deb8d356',
    'base': 'ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66',
    'small': '536b0662742c02347bc0e980a01041f333bce120',
}
_MODEL_NAMES = frozenset(_MODEL_REVISIONS)
_UNSUPPORTED_MODEL_REASON = (
    'Diese Modellgröße wird innerhalb der sicheren Speichergrenze nicht unterstützt. '
    'Bitte tiny, base oder small als Sprachmodell wählen.'
)


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
    name = stt_model_name()
    if name not in _MODEL_NAMES:
        return {'available': False, 'reason': _UNSUPPORTED_MODEL_REASON}
    return {'available': True, 'model': name}


def _get_model_path():
    """Obtain trusted model files before launching the offline audio worker."""
    from django.conf import settings
    name = stt_model_name()
    if name not in _MODEL_NAMES:
        raise TranscriptionUnavailable(_UNSUPPORTED_MODEL_REASON)
    revision = _MODEL_REVISIONS[name]
    try:
        from faster_whisper.utils import download_model
    except ImportError as exc:
        raise TranscriptionUnavailable(
            'Lokale Spracherkennung nicht verfügbar: faster-whisper ist nicht installiert.'
        ) from exc
    try:
        model_root = Path(settings.SEDO_HOME).resolve() / 'whisper-models'
        # Old unversioned caches never silently satisfy a pinned-model request.
        model_dir = model_root / f'{name}-{revision}'
        # Reject redirected locations before the downloader can write anything.
        # Keep the second check as well: downloaded/cached files must remain
        # inside the dedicated model directory when handed to the worker.
        if (model_root.is_symlink() or model_dir.is_symlink()
                or any(p.is_symlink() for p in model_dir.rglob('*'))):
            raise ValueError('Model directory contains links')
        model_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Explicit local directory avoids Hugging Face cache symlinks escaping
        # the worker's model-only read allowlist. No account token is used.
        required = ('model.bin', 'config.json', 'tokenizer.json')
        if not all((model_dir / f).is_file() for f in required):
            download_model('Systran/faster-whisper-' + name, revision=revision,
                           output_dir=str(model_dir), use_auth_token=False)
        if (model_root.is_symlink() or model_dir.is_symlink()
                or any(p.is_symlink() for p in model_dir.rglob('*'))):
            raise ValueError('Model directory contains links')
        return model_dir.resolve()
    except Exception as exc:  # Modell-Download/Initialisierung fehlgeschlagen
        raise TranscriptionUnavailable(
            f'Das Whisper-Modell „{name}“ konnte nicht geladen werden. Bitte die lokale Modellinstallation prüfen.'
        ) from exc


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

    from ..document_probe import DocumentProbeError, run_operation
    if not _TRANSCRIPTION_SLOT.acquire(blocking=False):
        raise TranscriptionUnavailable('Es wird bereits ein Diktat verarbeitet. Bitte kurz warten.')
    try:
        result = run_operation('transcribe', [audio_bytes], model_path=str(_get_model_path()),
                               prompt=STT_INITIAL_PROMPT)
        text = result.get('text')
        if not isinstance(text, str) or len(text) > 50_000:
            raise ValueError('Ungültiges Ergebnis der Spracherkennung.')
        return text
    except DocumentProbeError as exc:
        raise ValueError('Die Aufnahme konnte nicht innerhalb der sicheren Grenzen verarbeitet werden. Bitte kürzer diktieren.') from exc
    finally:
        _TRANSCRIPTION_SLOT.release()
