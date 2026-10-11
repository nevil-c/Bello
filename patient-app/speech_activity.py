"""Speech-aware endpointing without loading another transcription model."""
from collections import deque
import os

class SpeechActivity:
    def __init__(self,min_rms=None):
        import webrtcvad
        self.vad=webrtcvad.Vad(3)
        self.min_rms=float(os.environ.get("BELLO_MIN_SPEECH_RMS","450")) if min_rms is None else min_rms
        self.speech=False
        self.rms=0.0
        self.elapsed=0.0
        self.last_speech=None
        self.recent=deque(maxlen=3)
        self.voice_level=0.0

    def accept(self,pcm):
        # 16 kHz mono PCM16, five 20 ms frames in each 100 ms microphone block.
        votes=[self.vad.is_speech(pcm[i:i+640],16000)
               for i in range(0,len(pcm)-639,640)]
        self.elapsed+=len(pcm)/32000
        # Reject steady narrow-band tones such as electrical hum or the start chime.
        import numpy as np
        samples=np.frombuffer(pcm,dtype='<i2').astype(float)
        spectrum=np.abs(np.fft.rfft(samples*np.hanning(len(samples))))**2
        total=spectrum.sum()
        tonal=total>0 and np.partition(spectrum,-3)[-3:].sum()/total>.92
        self.rms=float(np.sqrt(np.mean(samples*samples)))
        threshold=max(self.min_rms,self.voice_level*.30)
        speech=sum(votes)>=4 and not tonal and self.rms>=threshold
        self.speech=speech
        self.recent.append(speech)
        if speech and sum(self.recent)>=2:
            self.last_speech=self.elapsed
            self.voice_level=max(self.rms,self.voice_level*.98)
        if self.last_speech is not None:
            return 'done' if self.elapsed-self.last_speech>=2 else 'listening'
        return 'empty' if self.elapsed>=10 else 'listening'

    @property
    def pause_progress(self):
        if self.last_speech is None:return 0.0
        return min(1.0,max(0.0,(self.elapsed-self.last_speech)/2.0))
