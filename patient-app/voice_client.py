"""Microphone capture and authenticated Bello upload. Provider keys stay on server."""
import io
import json
import os
import time
import threading
import wave
from urllib.request import Request, urlopen
from urllib.error import HTTPError

class Cancelled(Exception):
    pass

def api(path, body=None, content_type='application/json'):
    token=os.environ.get('BELLO_API_TOKEN')
    if not token:
        raise RuntimeError('Configure BELLO_API_TOKEN before starting the patient app.')
    server=os.environ.get('BELLO_SERVER','http://127.0.0.1:8766').rstrip('/')
    request=Request(server+path,data=body,headers={'X-Bello-Token':token,'Content-Type':content_type})
    try:
        with urlopen(request,timeout=150) as response:
            return json.load(response)
    except HTTPError as error:
        try: message=json.load(error).get('error','Upload failed')
        except ValueError: message='Upload failed'
        raise RuntimeError(message) from None

def submit_audio(audio, room, cancel, request_id=None):
    if cancel.is_set(): raise Cancelled()
    request={'request_id':request_id} if request_id is not None else api('/api/requests',json.dumps({'room':room}).encode())
    if cancel.is_set(): raise Cancelled()
    result=api('/api/requests/%s/audio'%request['request_id'],audio,'audio/wav')
    return wait_for_transcript(request['request_id'],cancel)

def wait_for_transcript(request_id,cancel,timeout=600):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if cancel.is_set():raise Cancelled()
        result=api(f'/api/requests/{request_id}')
        if result.get('transcript'):return result
        if result['state']=='Retry required':raise RuntimeError(result.get('error') or 'Local transcription failed.')
        cancel.wait(1)
    raise RuntimeError(f'Request {request_id} is still unconfirmed. Check the clerk dashboard before recording again.')

def play_recording_ding(sd):
    """Sustained local chime; playback finishes before microphone capture."""
    try:
        import numpy as np
        rate=16000
        t=np.arange(rate)/rate
        # Smooth attack and a gradual fade whose slope reaches zero at silence.
        phase=np.linspace(0,1,rate)
        fade=1-(6*phase**5-15*phase**4+10*phase**3)
        attack=np.sin(np.pi/2*np.clip(t/.02,0,1))**2
        tone=.85*np.sin(2*np.pi*880*t)*np.exp(-2*t)*fade*attack
        tone[-1]=0
        sd.play(tone.astype('float32'),samplerate=rate,blocking=True)
    except Exception:
        pass  # An unavailable speaker must not prevent microphone recording.

def record(cancel, on_audio=None):
    try:
        import sounddevice as sd
    except ImportError:
        raise RuntimeError('Microphone support is missing. Install patient-app/requirements.txt.') from None
    sd.check_input_settings(samplerate=16000,channels=1,dtype='int16')
    play_recording_ding(sd)
    if cancel.is_set():raise Cancelled()
    chunks=[]; started=time.monotonic(); last_voice=None; last_partial=started
    # 16 kHz mono signed 16-bit PCM; stop after 2 seconds of silence following speech.
    with sd.RawInputStream(samplerate=16000,channels=1,dtype='int16',blocksize=1600) as stream:
        while time.monotonic()-started<30:
            if cancel.is_set(): raise Cancelled()
            data,overflow=stream.read(1600)
            if overflow: raise RuntimeError('Microphone audio was interrupted. Please try again.')
            raw=bytes(data);chunks.append(raw)
            import struct
            values=struct.unpack('<%dh'%(len(raw)//2),raw)
            rms=(sum(v*v for v in values)/len(values))**.5
            if rms>350: last_voice=time.monotonic()
            if on_audio and last_voice and time.monotonic()-last_partial>=3:
                on_audio(wav_bytes(chunks));last_partial=time.monotonic()
            if last_voice and time.monotonic()-last_voice>2: break
            if last_voice is None and time.monotonic()-started>10: return None
    if last_voice is None: return None
    return wav_bytes(chunks)

def wav_bytes(chunks):
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b''.join(chunks))
    return out.getvalue()

def capture_and_submit(room,cancel,on_words,on_processing):
    request_id=api('/api/requests',json.dumps({'room':room}).encode())['request_id']
    stopped=threading.Event();lock=threading.Lock();latest=[None]
    def snapshot(audio):
        with lock:latest[0]=audio
    def previews():
        previous=''
        while not stopped.is_set() and not cancel.is_set():
            with lock:audio=latest[0];latest[0]=None
            try:
                result=api(f'/api/requests/{request_id}/preview',audio,'audio/wav') if audio else api(f'/api/requests/{request_id}/preview')
                text=result.get('text','')
                if text and text!=previous and not stopped.is_set() and not cancel.is_set():
                    on_words(text);previous=text
            except Exception:pass  # Optional partials must never block the final recording.
            stopped.wait(.5)
    thread=threading.Thread(target=previews,daemon=True);thread.start()
    try:audio=record(cancel,snapshot)
    finally:stopped.set();thread.join(timeout=1)
    if cancel.is_set():raise Cancelled()
    if audio is None:return None
    on_processing()
    return submit_audio(audio,room,cancel,request_id=request_id)


def submit_text(text, room):
    return api('/api/requests',json.dumps({'room':room,'text':text}).encode())

def submit_feedback(request_id, rating, resolution_answer, comment=''):
    return api(f'/api/requests/{request_id}/feedback',json.dumps({
        'rating':rating,'resolution_answer':resolution_answer,'comment':comment}).encode())
