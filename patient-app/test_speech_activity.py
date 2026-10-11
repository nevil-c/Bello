import unittest
from unittest.mock import patch
from speech_activity import SpeechActivity

class Tests(unittest.TestCase):
 def block(self):return b'\x00\x00'*1600
 def test_noise_does_not_keep_speech_open(self):
  activity=SpeechActivity(min_rms=0)
  with patch.object(activity.vad,'is_speech',return_value=True):
   activity.accept(self.block());activity.accept(self.block())
  with patch.object(activity.vad,'is_speech',return_value=False):
   for _ in range(21):result=activity.accept(self.block())
  self.assertEqual(result,'done')
 def test_empty_requires_no_speech_not_zero_volume(self):
  activity=SpeechActivity(min_rms=0)
  with patch.object(activity.vad,'is_speech',return_value=False):
   for _ in range(101):result=activity.accept(b'\xff\x01'*1600)
  self.assertEqual(result,'empty')
 def test_steady_hum_is_not_speech(self):
  import numpy as np
  activity=SpeechActivity(min_rms=0)
  for block in range(101):
   t=(np.arange(1600)+1600*block)/16000
   pcm=(900*np.sin(2*np.pi*120*t)).astype('<i2').tobytes()
   result=activity.accept(pcm)
  self.assertEqual(result,'empty')
 def test_brief_speech_pause_does_not_stop(self):
  activity=SpeechActivity(min_rms=0)
  with patch.object(activity.vad,'is_speech',return_value=True):
   activity.accept(self.block());activity.accept(self.block())
  with patch.object(activity.vad,'is_speech',return_value=False):
   for _ in range(10):result=activity.accept(self.block())
  self.assertEqual(result,'listening')
if __name__=='__main__':unittest.main()
