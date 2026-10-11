"""Local Vosk partial results; final saved text still comes from Whisper."""
import json
import os
from pathlib import Path
import threading

MODEL_DIR=Path(os.environ.get('BELLO_STREAM_MODEL',str(Path(__file__).parent/'models/vosk-model-small-en-us-0.15')))
_model=None
_lock=threading.Lock()

def load_model():
    global _model
    with _lock:
        if _model is None:
            if not (MODEL_DIR/'am/final.mdl').is_file():
                raise RuntimeError('Streaming model missing. Run prepare_stream_model.py first.')
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            _model=Model(str(MODEL_DIR))
    return _model

class StreamDecoder:
    def __init__(self):
        from vosk import KaldiRecognizer
        self.recognizer=KaldiRecognizer(load_model(),16000)
        self.segments=[]
        self.last=''

    def feed(self,pcm):
        if self.recognizer.AcceptWaveform(pcm):
            text=json.loads(self.recognizer.Result()).get('text','')
            if text:self.segments.append(text)
            partial=''
        else:partial=json.loads(self.recognizer.PartialResult()).get('partial','')
        return self.changed(' '.join(self.segments+[partial]).strip())

    def finish(self):
        text=json.loads(self.recognizer.FinalResult()).get('text','')
        return self.changed(' '.join(self.segments+[text]).strip())

    def changed(self,text):
        if text and text!=self.last:self.last=text;return text
        return None
