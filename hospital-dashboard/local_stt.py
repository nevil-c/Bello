"""Offline CPU transcription. Download model explicitly with prepare_model.py."""
import io
import os
from pathlib import Path
import threading

MODEL_DIR=Path(os.environ.get('BELLO_MODEL_DIR',str(Path(__file__).parent/'models/tiny')))
_model=None
_lock=threading.Lock()

def load_model():
    global _model
    with _lock:
        if _model is None:
            if not (MODEL_DIR/'model.bin').is_file():
                raise RuntimeError('Local speech model missing. Run prepare_model.py first.')
            from faster_whisper import WhisperModel
            _model=WhisperModel(str(MODEL_DIR),device='cpu',compute_type='int8',
                cpu_threads=int(os.environ.get('BELLO_STT_THREADS','3')),num_workers=1,local_files_only=True)
    return _model

def transcribe(audio,extension):
    model=load_model()
    segments,info=model.transcribe(io.BytesIO(audio),beam_size=1,task='transcribe',
        vad_filter=True,condition_on_previous_text=False)
    text=' '.join(segment.text.strip() for segment in segments).strip()
    if not text:raise RuntimeError('No speech detected. No transcript was saved.')
    return {'text':text,'language':info.language}
