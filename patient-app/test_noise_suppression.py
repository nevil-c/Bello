import unittest
import numpy as np
from unittest.mock import patch
from noise_suppression import NoiseSuppressor
from speech_activity import SpeechActivity

class Tests(unittest.TestCase):
 def test_stationary_noise_attenuation_and_pcm_length(self):
  rng=np.random.default_rng(4);n=NoiseSuppressor();raw=[];clean=[]
  for _ in range(20):
   block=rng.normal(0,200,1600).astype('<i2').tobytes()
   out=n.process(block,False)
   self.assertEqual(len(out),len(block));raw.append(block);clean.append(out)
  rms=lambda b:np.sqrt(np.mean(np.frombuffer(b,dtype='<i2').astype(float)**2))
  self.assertLess(rms(b''.join(clean[5:])),rms(b''.join(raw[5:]))*.2)
 def test_quiet_input_rejected_even_with_voice_votes(self):
  a=SpeechActivity(min_rms=220)
  rng=np.random.default_rng(3)
  with patch.object(a.vad,'is_speech',return_value=True):
   for _ in range(3):a.accept(rng.normal(0,80,1600).astype('<i2').tobytes())
  self.assertIsNone(a.last_speech)
 def test_speech_is_preserved_after_overlap_delay(self):
  n=NoiseSuppressor();rng=np.random.default_rng(7)
  x=rng.normal(0,2000,1600).astype('<i2')
  output=np.frombuffer(n.process(x.tobytes(),True),dtype='<i2')
  self.assertGreater(np.sqrt(np.mean(output[640:].astype(float)**2)),1000)
if __name__=='__main__':unittest.main()
