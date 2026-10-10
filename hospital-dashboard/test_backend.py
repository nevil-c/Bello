import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import backend as b
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);b.ROOT=Path(self.tmp.name);b.DATA=b.ROOT/'data';(b.ROOT/'dist').mkdir();(b.ROOT/'dist/index.html').write_text("{id:8,request:'Sample blanket request',urgency:2,room:'203 B'}");b.initialize()
  self.env=patch.dict(os.environ,{'ELEVENLABS_API_KEY':'fake-test-key'});self.env.start();self.addCleanup(self.env.stop)
 def post(self,path,body=b'',token=True):
  h=object.__new__(b.Handler);h.path=path;h.headers={'Content-Length':str(len(body))};h.rfile=io.BytesIO(body)
  if token:h.headers['X-Bello-Token']=b.TOKEN
  out=[];h.send=lambda *args:out.append(args);h.do_POST();return out[0]
 def upload(self):return self.post('/api/requests/8/audio',b'RIFF0000WAVEfixture')
 def dispatch(self):return self.post('/api/requests/8/dispatch',json.dumps({'department':'Housekeeping & Facilities','transcript':'tampered text'}).encode())
 def test_auth_and_missing_transcript(self):
  self.assertEqual(self.post('/api/requests/8/audio',token=False)[0],401);self.assertEqual(self.dispatch()[0],409)
 def test_pipeline_saved_text_duplicate_ack(self):
  with patch.object(b,'transcribe',return_value={'text':'Please bring a clean blanket.','language':'eng'}) as api:
   self.assertEqual(self.upload()[0],201);self.assertEqual(self.upload()[0],409);api.assert_called_once()
  result=self.dispatch();self.assertEqual(result[0],201);self.assertEqual(self.dispatch()[0],409)
  with b.db() as c:r=c.execute('SELECT * FROM deliveries').fetchone()
  self.assertEqual(r['transcript'],'Please bring a clean blanket.')
  self.assertEqual(self.post('/api/deliveries/'+result[1]['delivery_id']+'/acknowledge')[0],200)
 def test_failed_transcription_not_delivered(self):
  with patch.object(b,'transcribe',side_effect=RuntimeError('Provider unavailable')):self.assertEqual(self.upload()[0],502)
  self.assertEqual(self.dispatch()[0],409)
 def test_provider_request(self):
  class Response(io.BytesIO):
   def __enter__(self):return self
   def __exit__(self,*args):self.close()
  with patch.object(b,'urlopen',return_value=Response(b'{"text":"A blanket please","language_code":"eng"}')) as api:
   self.assertEqual(b.transcribe(b'fake','wav')['text'],'A blanket please')
   req=api.call_args.args[0];self.assertEqual(req.full_url,'https://api.elevenlabs.io/v1/speech-to-text');self.assertIn(b'scribe_v2',req.data)
if __name__=='__main__':unittest.main()
