import json
import os
import threading
import unittest
from unittest.mock import patch
import streaming_stt as stt
import voice_client as client

class Tests(unittest.TestCase):
 def setUp(self):
  engine=patch.dict(os.environ,{"BELLO_LIVE_ENGINE":"vosk"})
  engine.start();self.addCleanup(engine.stop)
 def test_partial_revisions_and_committed_segments(self):
  class Recognizer:
   def AcceptWaveform(self,pcm):return pcm==b'end'
   def PartialResult(self):return json.dumps({'partial':'bring water'})
   def Result(self):return json.dumps({'text':'bring water'})
   def FinalResult(self):return json.dumps({'text':'please'})
  decoder=object.__new__(stt.StreamDecoder);decoder.recognizer=Recognizer();decoder.segments=[];decoder.last=''
  self.assertEqual(decoder.feed(b'a'),'bring water')
  self.assertIsNone(decoder.feed(b'a'))
  self.assertIsNone(decoder.feed(b'end'))
  self.assertEqual(decoder.finish(),'bring water please')
 def test_capture_does_not_wait_for_backend_and_partials_are_not_saved(self):
  captured=threading.Event();words=[]
  class Decoder:
   def feed(self,pcm):return 'live words'
   def finish(self):return 'live words revised'
  def api(*args):
   self.assertTrue(captured.wait(2))
   return {'request_id':44}
  def record(cancel,on_pcm=None):
   captured.set();on_pcm(b'pcm');return b'WAV'
  with patch.object(stt,'StreamDecoder',Decoder),patch.object(client,'record',side_effect=record),patch.object(client,'api',side_effect=api),patch.object(client,'submit_audio',return_value={'transcript':'final saved words'}) as final:
   result=client.capture_and_submit('412 B',threading.Event(),words.append,lambda:None)
  self.assertIn('live words',words)
  self.assertEqual(result['transcript'],'final saved words')
  final.assert_called_once()
  self.assertEqual(final.call_args.kwargs['request_id'],44)
 def test_cancel_prevents_final_upload(self):
  class Decoder:
   def finish(self):return None
  cancel=threading.Event();cancel.set()
  with patch.object(stt,'StreamDecoder',Decoder),patch.object(client,'api',return_value={'request_id':1}),patch.object(client,'record',side_effect=client.Cancelled),patch.object(client,'submit_audio') as final:
   with self.assertRaises(client.Cancelled):client.capture_and_submit('412',cancel,lambda t:None,lambda:None)
  final.assert_not_called()
if __name__=='__main__':unittest.main()
