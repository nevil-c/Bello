"""Lightweight spectral noise reduction and soft gating; no learned model.
BELLO_NOISE_GATE  gain applied while the gate is shut (default .005, about -46 dB).
BELLO_NOISE_FLOOR floor of the spectral attenuation (default .12).
Both are microphones' problem: a quiet or noisy mic needs its own pair."""
import os
import numpy as np

class NoiseSuppressor:
    def __init__(self):
        self.previous=np.zeros(320)
        self.overlap=np.zeros(320)
        self.window=np.sqrt(np.hanning(641)[:-1])
        self.noise=None
        self.gain=0.0
        self.hold=0
        self.shut=float(os.environ.get("BELLO_NOISE_GATE",".005"))
        self.floor=float(os.environ.get("BELLO_NOISE_FLOOR",".12"))
        self.frequency=np.fft.rfftfreq(640,1/16000)

    def process(self,pcm,speech):
        samples=np.frombuffer(pcm,dtype='<i2').astype(float)
        output=[]
        if speech:self.hold=2  # Preserve quiet consonants for 200 ms after speech.
        else:self.hold=max(0,self.hold-1)
        target=1.0 if speech or self.hold else self.shut
        for offset in range(0,len(samples),320):
            current=samples[offset:offset+320]
            if len(current)!=320:raise ValueError('Expected 20 ms PCM frames')
            frame=np.concatenate((self.previous,current))*self.window
            self.previous=current.copy()
            spectrum=np.fft.rfft(frame)
            power=np.abs(spectrum)**2
            if self.noise is None:self.noise=np.zeros_like(power)
            if not speech:self.noise=.85*self.noise+.15*power
            attenuation=np.clip(1-1.8*self.noise/(power+1e-8),self.floor,1)
            attenuation[self.frequency<180]*=.05
            reconstructed=np.fft.irfft(spectrum*attenuation,n=640)*self.window
            block=reconstructed[:320]+self.overlap
            self.overlap=reconstructed[320:]
            block*=np.linspace(self.gain,target,320)
            self.gain=target
            output.append(block)
        return np.clip(np.concatenate(output),-32768,32767).astype('<i2').tobytes()
