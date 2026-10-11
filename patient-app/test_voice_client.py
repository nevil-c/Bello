import threading
import unittest
from unittest.mock import patch
import voice_client as client

class Tests(unittest.TestCase):
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
if __name__=='__main__':unittest.main()
