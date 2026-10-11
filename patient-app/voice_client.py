"""Microphone capture and authenticated Bello upload. Provider keys stay on server.

BELLO_TRANSPORT  http (default): talk to BELLO_SERVER over the network.
                 ble: talk over Bluetooth LE to the relay on the server (../ble-link). Recordings are sent
                 Opus-compressed and the live partial text is off.
"""
import io
import json
import os
import sys
import time
import threading
import queue
import wave
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from urllib.parse import urlencode

# The patient screen's language (set from the accessibility menu). Sent with each request; the
# server still detects the spoken language itself.
UI_LANGUAGE='en'
TRANSPORT=os.environ.get('BELLO_TRANSPORT','http')
_link=None
_link_lock=threading.Lock()

class Cancelled(Exception):
    pass

def new_request(room,**fields):
    return api('/api/requests',json.dumps({'room':room,'ui_language':UI_LANGUAGE,**fields}).encode())

def ble_link():
    """The Bluetooth link, started once. The Pi starts advertising as soon as the app opens."""
    global _link
    with _link_lock:
        if _link is None:
            sys.path.insert(0,os.path.join(os.path.dirname(os.path.abspath(__file__)),'..','ble-link'))
            from pi_link import PiLink
            _link=PiLink().start()
    return _link

def start_transport():
    if TRANSPORT=='ble':
        try:ble_link()
        except Exception as error:print(f'Bluetooth unavailable: {error}',file=sys.stderr,flush=True)

def ble_api(path, body, content_type, timeout):
    from protocol import LinkError
    try:
        status,_,data=ble_link().request('POST' if body is not None else 'GET',path,body,content_type,timeout)
    except LinkError as error:
        raise RuntimeError(str(error)) from None
    try:payload=json.loads(data) if data else {}
    except ValueError:payload={}
    if status>=400:raise RuntimeError(payload.get('error','Upload failed'))
    return payload

def api(path, body=None, content_type='application/json', timeout=150):
    if TRANSPORT=='ble':return ble_api(path,body,content_type,timeout)
    token=os.environ.get('BELLO_API_TOKEN')
    if not token:
        raise RuntimeError('Configure BELLO_API_TOKEN before starting the patient app.')
    server=os.environ.get('BELLO_SERVER','http://127.0.0.1:8766').rstrip('/')
    request=Request(server+path,data=body,headers={'X-Bello-Token':token,'Content-Type':content_type})
    try:
        with urlopen(request,timeout=timeout) as response:
            return json.load(response)
    except HTTPError as error:
        try: message=json.load(error).get('error','Upload failed')
        except ValueError: message='Upload failed'
        raise RuntimeError(message) from None

def submit_audio(audio, room, cancel, request_id=None):
    if cancel.is_set(): raise Cancelled()
    request={'request_id':request_id} if request_id is not None else new_request(room)
    if cancel.is_set(): raise Cancelled()
    if TRANSPORT=='ble':
        result=api('/api/requests/%s/audio'%request['request_id'],compress(audio),'audio/ogg')
    else:
        result=api('/api/requests/%s/audio'%request['request_id'],audio,'audio/wav')
    return wait_for_transcript(request['request_id'],cancel)

def wait_for_transcript(request_id,cancel,timeout=600):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if cancel.is_set():raise Cancelled()
        result=api(f'/api/requests/{request_id}')
        if result.get('transcript'):return result
        if result['state']=='Retry required' and result.get('error')=='No speech detected. No transcript was saved.':return None
        if result['state']=='Retry required':raise RuntimeError(result.get('error') or 'Local transcription failed.')
        cancel.wait(1)
    raise RuntimeError(f'Request {request_id} is still unconfirmed. Check the clerk dashboard before recording again.')

def compress(wav):
    """WAV -> Ogg/Opus at 24 kbps (about 3 KB per second of speech), for the Bluetooth link.
    Falls back to FFmpeg's own Opus encoder, then lossless FLAC, if this PyAV lacks libopus."""
    import av
    import numpy as np
    with wave.open(io.BytesIO(wav)) as source:
        rate=source.getframerate();pcm=np.frombuffer(source.readframes(source.getnframes()),dtype=np.int16)
    for codec,options in (('libopus',{'b':'24000'}),('opus',{'b':'24000','strict':'experimental'}),('flac',{})):
        try:return encode_ogg(pcm,rate,codec,options)
        except Exception:continue
    raise RuntimeError('No audio encoder available for the Bluetooth link')

def encode_ogg(pcm,rate,codec,options):
    import av
    import numpy as np
    out=io.BytesIO()
    with av.open(out,'w',format='ogg') as container:
        stream=container.add_stream(codec,rate=48000 if codec=='opus' else rate,options=options)
        stream.layout='mono'
        resampler=av.AudioResampler(format=stream.format.name if stream.format else 's16',layout='mono',rate=stream.rate)
        step=rate//50   # 20 ms frames, the size Opus encodes
        for start in range(0,len(pcm),step):
            block=pcm[start:start+step]
            if len(block)<step:block=np.pad(block,(0,step-len(block)))
            frame=av.AudioFrame.from_ndarray(block.reshape(1,-1),format='s16',layout='mono')
            frame.sample_rate=rate;frame.pts=start
            for converted in resampler.resample(frame):
                for packet in stream.encode(converted):container.mux(packet)
        for converted in resampler.resample(None):
            for packet in stream.encode(converted):container.mux(packet)
        for packet in stream.encode(None):container.mux(packet)
    return out.getvalue()

def recording_chime(rate):
    """A one-second bell with a soft attack and a silent, smooth tail."""
    import numpy as np
    t=np.arange(rate,dtype=np.float64)/rate
    phase=np.linspace(0,1,rate)
    fade=1-(6*phase**5-15*phase**4+10*phase**3)
    attack=np.sin(np.pi/2*np.clip(t/.025,0,1))**2
    bell=(np.sin(2*np.pi*660*t)+.16*np.sin(2*np.pi*1320*t)*np.exp(-4*t)
          +.05*np.sin(2*np.pi*1980*t)*np.exp(-6*t))
    tone=.75*bell*np.exp(-1.8*t)*fade*attack
    tone[0]=tone[-1]=0
    return tone.astype('float32')

def play_recording_ding(sd):
    """Play at the speaker's native rate, independently of microphone capture."""
    try:
        import numpy as np
        device=os.environ.get('BELLO_OUTPUT_DEVICE')
        if device is not None and device.isdigit():device=int(device)
        info=sd.query_devices(device,'output')
        rate=int(info['default_samplerate'])
        channels=min(2,int(info['max_output_channels']))
        if channels<1:return
        sd.check_output_settings(device=device,samplerate=rate,channels=channels,dtype='float32')
        tone=recording_chime(rate)
        if channels==2:tone=np.repeat(tone[:,None],2,axis=1)
        sd.play(tone,samplerate=rate,device=device,blocking=True)
    except Exception as error:
        print(f'Recording chime unavailable: {error}',flush=True)
        # An unavailable speaker must not prevent microphone recording.

def microphone_settings(sd):
    """Prefer recognition rate, falling back to the microphone's native rate."""
    device=os.environ.get('BELLO_INPUT_DEVICE')
    if device is not None and device.isdigit():device=int(device)
    try:
        sd.check_input_settings(device=device,samplerate=16000,channels=1,dtype='int16')
        return device,16000
    except sd.PortAudioError:
        rate=int(sd.query_devices(device,'input')['default_samplerate'])
        sd.check_input_settings(device=device,samplerate=rate,channels=1,dtype='int16')
        return device,rate

class MicrophonePCM:
    """Stateful, filtered conversion to exact 100 ms recognition blocks."""
    def __init__(self,rate):
        self.rate=rate
        self.pending=bytearray()
        if rate!=16000:
            import av
            self.resampler=av.AudioResampler(format='s16',layout='mono',rate=16000)
    def read(self,stream,cancel):
        while len(self.pending)<3200:
            if cancel.is_set():raise Cancelled()
            data,overflow=stream.read(self.rate//10)
            if overflow:raise RuntimeError('Microphone audio was interrupted. Please try again.')
            if self.rate==16000:self.pending.extend(bytes(data))
            else:
                import av
                import numpy as np
                frame=av.AudioFrame.from_ndarray(np.frombuffer(bytes(data),dtype=np.int16).reshape(1,-1),format='s16',layout='mono')
                frame.sample_rate=self.rate
                for converted in self.resampler.resample(frame):
                    self.pending.extend(converted.to_ndarray().tobytes())
        result=bytes(self.pending[:3200]);del self.pending[:3200]
        return result

def record(cancel, on_audio=None, on_pcm=None, on_pause=None,on_level=None):
    try:
        import sounddevice as sd
    except ImportError:
        raise RuntimeError('Microphone support is missing. Install patient-app/requirements.txt.') from None
    device,rate=microphone_settings(sd)
    pcm=MicrophonePCM(rate)
    if cancel.is_set():raise Cancelled()
    from speech_activity import SpeechActivity
    activity=SpeechActivity()
    from noise_suppression import NoiseSuppressor
    suppressor=NoiseSuppressor()
    denoise=os.environ.get("BELLO_NOISE_SUPPRESSION","1")!="0"
    chunks=[]; started=time.monotonic(); last_partial=started
    # Preserve all audio, but end on two seconds without detected speech, even with background noise.
    with sd.RawInputStream(device=device,samplerate=rate,channels=1,dtype='int16',blocksize=rate//10) as stream:
        threading.Thread(target=play_recording_ding,args=(sd,),daemon=True).start()
        while time.monotonic()-started<30:
            if cancel.is_set(): raise Cancelled()
            raw=pcm.read(stream,cancel)
            endpoint=activity.accept(raw)
            cleaned=suppressor.process(raw,activity.speech) if denoise else raw
            chunks.append(cleaned)
            if on_pcm:on_pcm(cleaned)
            if on_pause:on_pause(activity.pause_progress)
            if on_level:on_level(activity.rms if activity.speech else 0.0)
            if on_audio and activity.last_speech is not None and time.monotonic()-last_partial>=3:
                on_audio(wav_bytes(chunks));last_partial=time.monotonic()
            if endpoint=='done':break
            if endpoint=='empty':return None
    if activity.last_speech is None:return None
    return wav_bytes(chunks)

def wav_bytes(chunks):
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(16000);wav.writeframes(b''.join(chunks))
    return out.getvalue()

def capture_batch(room,cancel,on_words,on_processing,on_pause=None,on_level=None):
    # Backend setup runs in parallel; it must not delay microphone activation.
    request_id=None;setup_error=[];ready=threading.Event()
    stopped=threading.Event();lock=threading.Lock();latest=[None]
    def snapshot(audio):
        with lock:latest[0]=audio
    def previews():
        nonlocal request_id
        try:request_id=new_request(room)['request_id']
        except Exception as error:setup_error.append(error)
        finally:ready.set()
        if setup_error:return
        if TRANSPORT=='ble':return  # no live partial text over Bluetooth
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
    live=snapshot if TRANSPORT!='ble' else None
    try:audio=record(cancel,live,on_pause=on_pause,on_level=on_level) if on_pause or on_level else record(cancel,live)
    finally:stopped.set();thread.join(timeout=1)
    if cancel.is_set():raise Cancelled()
    if audio is None:return None
    on_processing()
    while not ready.wait(.1):
        if cancel.is_set():raise Cancelled()
    if setup_error:raise setup_error[0]
    return submit_audio(audio,room,cancel,request_id=request_id)


def submit_text(text, room):
    return new_request(room,text=text)

def submit_feedback(request_id, rating, resolution_answer, comment=''):
    return api(f'/api/requests/{request_id}/feedback',json.dumps({
        'rating':rating,'resolution_answer':resolution_answer,'comment':comment}).encode())


def submit_empty(room):
    return new_request(room,confirmed_empty=True)


def resolve_room(room):
    """Staff at the bedside: mark every open request for this bed as done."""
    return api('/api/rooms/resolve',json.dumps({'room':room}).encode())


def open_requests(room):
    return api('/api/rooms/open?'+urlencode({'room':room}),timeout=10)['open']


def ui_strings(language):
    """Translated screen strings from the server; the first translation of a language can take a while."""
    return api(f'/api/i18n/{language}',timeout=300)['strings']


def capture_and_submit(room,cancel,on_words,on_processing,on_pause=None,on_level=None):
    if os.environ.get('BELLO_LIVE_ENGINE','whisper')!='vosk':
        return capture_batch(room,cancel,on_words,on_processing,on_pause=on_pause,on_level=on_level)
    from streaming_stt import StreamDecoder
    decoder=StreamDecoder()  # Model is preloaded when the UI opens.
    frames=queue.Queue(maxsize=100)
    done=threading.Event();ready=threading.Event();errors=[];request_id=[None]
    def setup():
        try:request_id[0]=new_request(room)['request_id']
        except Exception as error:errors.append(error)
        finally:ready.set()
    threading.Thread(target=setup,daemon=True).start()
    def decode():
        try:
            while not done.is_set() or not frames.empty():
                if cancel.is_set():return
                try:pcm=frames.get(timeout=.1)
                except queue.Empty:continue
                text=decoder.feed(pcm)
                if text and not cancel.is_set():on_words(text)
            text=decoder.finish()
            if text and not cancel.is_set():on_words(text)
        except Exception as error:errors.append(error)
    thread=threading.Thread(target=decode,daemon=True);thread.start()
    def push(pcm):
        try:frames.put_nowait(pcm)
        except queue.Full:raise RuntimeError('Live recognition cannot keep up. Please try again.')
    try:audio=record(cancel,on_pcm=push,on_pause=on_pause,on_level=on_level) if on_pause or on_level else record(cancel,on_pcm=push)
    finally:done.set();thread.join(timeout=10)
    if cancel.is_set():raise Cancelled()
    if thread.is_alive():raise RuntimeError('Live recognition did not finish. Please try again.')
    if audio is None:return None
    on_processing()
    while not ready.wait(.1):
        if cancel.is_set():raise Cancelled()
    if errors:raise errors[0]
    return submit_audio(audio,room,cancel,request_id=request_id[0])
