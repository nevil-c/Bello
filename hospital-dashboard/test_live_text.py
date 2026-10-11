import unittest
from unittest.mock import patch
import backend as b
from test_backend import Tests as Helpers

class LiveTests(unittest.TestCase):
 setUp=Helpers.setUp
 post=Helpers.post
 def tearDown(self):
  with b.PREVIEW_LOCK:b.PREVIEWS.clear()
 def test_latest_snapshot_is_provisional_and_final_supersedes(self):
  route='/api/requests/8/preview'
  self.assertEqual(self.post(route,b'RIFF0000WAVEfirst')[0],202)
  self.assertEqual(self.post(route,b'RIFF0000WAVEnewest')[0],202)
  with patch.object(b,'transcribe',return_value={'text':'Partial words','language':'en'}) as model:
   self.assertTrue(b.process_preview())
   self.assertEqual(model.call_args.args[0],b'RIFF0000WAVEnewest')
  with b.db() as c:self.assertIsNone(c.execute('SELECT transcript FROM requests WHERE id=8').fetchone()[0])
  self.assertEqual(Helpers.dispatch(self)[0],409)
  self.assertEqual(Helpers.upload(self)[0],202)
  self.assertNotIn(8,b.PREVIEWS)
  self.assertEqual(self.post(route,b'RIFF0000WAVElate')[0],409)
 def test_silence_does_not_fail_request(self):
  self.post('/api/requests/8/preview',b'RIFF0000WAVEquiet')
  with patch.object(b,'transcribe',side_effect=RuntimeError('No speech')):b.process_preview()
  with b.db() as c:self.assertEqual(c.execute('SELECT state FROM requests WHERE id=8').fetchone()[0],'Awaiting recording')
if __name__=='__main__':unittest.main()
