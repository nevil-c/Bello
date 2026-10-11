import threading
import unittest
from unittest.mock import patch
import voice_client as client

class Tests(unittest.TestCase):
 def test_chime_uses_native_output_rate_and_stereo(self):
  from unittest.mock import Mock
  import numpy as np
  sd=Mock()
  sd.query_devices.return_value={'default_samplerate':48000,'max_output_channels':2}
  client.play_recording_ding(sd)
  tone=sd.play.call_args.args[0]
  self.assertEqual(tone.shape,(48000,2))
  self.assertEqual(sd.play.call_args.kwargs['samplerate'],48000)
  self.assertTrue(np.all(tone[0]==0) and np.all(tone[-1]==0))
  self.assertLess(float(np.max(np.abs(tone))),1)
  self.assertLess(float(np.max(np.abs(tone[-480:]))),.0001)
 def test_chime_supports_mono_output(self):
  from unittest.mock import Mock
  sd=Mock()
  sd.query_devices.return_value={'default_samplerate':44100,'max_output_channels':1}
  client.play_recording_ding(sd)
  self.assertEqual(sd.play.call_args.args[0].shape,(44100,))
 def test_async_upload_polls_completed_transcript(self):
  results=[{'request_id':18},{'request_id':18,'state':'Queued'},
           {'request_id':18,'state':'Transcribing','transcript':None},
           {'request_id':18,'state':'Ready to send','transcript':'Please help','language':'en'}]
  cancel=threading.Event()
  with patch.object(client,'api',side_effect=results) as api,patch.object(cancel,'wait'):
   result=client.submit_audio(b'WAV','412 B',cancel)
  self.assertEqual(result['transcript'],'Please help')
  self.assertEqual(api.call_args_list[-1].args[0],'/api/requests/18')
 def test_live_capture_reuses_request_and_announces_processing(self):
  cancel=threading.Event();events=[]
  with patch.object(client,'api',side_effect=lambda path,*args: {'request_id':22} if path=='/api/requests' else {'text':''}),patch.object(client,'record',return_value=b'WAV'),patch.object(client,'submit_audio',return_value={'transcript':'Help'}) as final:
   result=client.capture_batch('412 B',cancel,lambda text:events.append(text),lambda:events.append('processing'))
  self.assertEqual(result['transcript'],'Help')
  self.assertEqual(final.call_args.kwargs['request_id'],22)
  self.assertEqual(events,['processing'])
 def test_cancel_before_upload(self):
  cancel=threading.Event();cancel.set()
  with patch.object(client,'api') as api:
   with self.assertRaises(client.Cancelled):client.submit_audio(b'WAV','412 B',cancel)
   api.assert_not_called()
 def test_failure_is_not_success(self):
  with patch.object(client,'api',return_value={'state':'Retry required','error':'No speech'}):
   with self.assertRaisesRegex(RuntimeError,'No speech'):client.wait_for_transcript(1,threading.Event())
 def test_typed_and_feedback_routes(self):
  with patch.object(client,'api',return_value={}) as api:
   client.submit_text('Blanket please','412 B');self.assertEqual(api.call_args.args[0],'/api/requests')
   client.submit_feedback(18,4,'yes');self.assertEqual(api.call_args.args[0],'/api/requests/18/feedback')
 def test_requests_carry_screen_language(self):
  with patch.object(client,'api',return_value={}) as api,patch.object(client,'UI_LANGUAGE','yue'):
   client.submit_text('Blanket please','412 B')
  import json
  self.assertEqual(json.loads(api.call_args.args[1]),{'room':'412 B','ui_language':'yue','text':'Blanket please'})
 def test_staff_resolve_and_open_routes(self):
  with patch.object(client,'api',return_value={'open':2}) as api:
   client.resolve_room('412 · B');self.assertEqual(api.call_args.args[0],'/api/rooms/resolve')
   self.assertEqual(client.open_requests('412 · B'),2);self.assertEqual(api.call_args.args[0],'/api/rooms/open?room=412+%C2%B7+B')
if __name__=='__main__':unittest.main()
