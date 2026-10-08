"""Untrusted audio decoding and speech recognition inside document_probe."""
import io
from pathlib import Path

MAX_DURATION_SECONDS = 300
MAX_TRANSCRIPT_CHARS = 50_000


def worker_transcribe(request, documents):
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    data, = documents
    model_path = Path(request['model_path'])
    if not model_path.is_dir() or not (model_path / 'model.bin').is_file():
        raise ValueError('Speech model unavailable')
    audio = decode_audio(io.BytesIO(data), sampling_rate=16000)
    if len(audio) > MAX_DURATION_SECONDS * 16000:
        raise ValueError('Recording exceeds duration limit')
    model = WhisperModel(str(model_path), device='cpu', compute_type='int8',
                         cpu_threads=2, num_workers=1, local_files_only=True)
    segments, _ = model.transcribe(audio, language='de',
        initial_prompt=request.get('prompt', '')[:2000], vad_filter=True, beam_size=5)
    parts, length = [], 0
    for segment in segments:
        text = segment.text.strip()
        length += len(text) + 1
        if length > MAX_TRANSCRIPT_CHARS:
            raise ValueError('Transcript exceeds limit')
        parts.append(text)
    return {'ok': True, 'text': ' '.join(parts).strip()}
