"""Offline CPU transcription. Download model explicitly with prepare_model.py.

BELLO_WHISPER_MODEL  model name under models/ (default large-v3-turbo; tiny for a Pi-only setup)
BELLO_MODEL_DIR      explicit model directory (overrides BELLO_WHISPER_MODEL)
BELLO_STT_THREADS    CPU threads for CTranslate2 (default: cores minus two, at most 8)
"""
import io
import os
from pathlib import Path
import threading

MODEL_NAME=os.environ.get('BELLO_WHISPER_MODEL','large-v3-turbo')
MODEL_DIR=Path(os.environ.get('BELLO_MODEL_DIR',str(Path(__file__).parent/'models'/MODEL_NAME)))
_model=None
_lock=threading.Lock()

def default_threads():
    return max(1,min(8,(os.cpu_count() or 4)-2))

def load_model():
    global _model
    with _lock:
        if _model is None:
            if not (MODEL_DIR/'model.bin').is_file():
                raise RuntimeError(f'Local speech model missing at {MODEL_DIR}. Run prepare_model.py first.')
            from faster_whisper import WhisperModel
            # CTranslate2 has no Apple GPU backend, so Whisper stays on CPU INT8 even on an M-series Mac.
            _model=WhisperModel(str(MODEL_DIR),device='cpu',compute_type='int8',
                cpu_threads=int(os.environ.get('BELLO_STT_THREADS',str(default_threads()))),num_workers=1,local_files_only=True)
    return _model

def transcribe(audio,extension):
    model=load_model()
    segments,info=model.transcribe(io.BytesIO(audio),beam_size=1,task='transcribe',
        vad_filter=True,condition_on_previous_text=False)
    text=' '.join(segment.text.strip() for segment in segments).strip()
    if not text:raise RuntimeError('No speech detected. No transcript was saved.')
    return {'text':text,'language':info.language}

def translate_to_english(audio,language=None):
    """Whisper's own speech -> English translation. Only full large-v3 (not turbo) was trained for it."""
    model=load_model()
    segments,_=model.transcribe(io.BytesIO(audio),beam_size=1,task='translate',language=language,
        vad_filter=True,condition_on_previous_text=False)
    text=' '.join(segment.text.strip() for segment in segments).strip()
    if not text:raise RuntimeError('Whisper translation returned no text')
    return text
