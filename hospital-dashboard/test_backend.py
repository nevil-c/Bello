import io,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import backend as b
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);b.ROOT=Path(self.tmp.name);b.DATA=b.ROOT/'data';(b.ROOT/'dist').mkdir();(b.ROOT/'dist/index.html').write_text("{id:8,request:'Sample blanket request',urgency:2,room:'203 B'}");b.initialize()
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
   self.assertEqual(self.upload()[0],202);self.assertEqual(self.upload()[0],409);api.assert_not_called();self.assertTrue(b.process_next());api.assert_called_once()
  result=self.dispatch();self.assertEqual(result[0],201);self.assertEqual(self.dispatch()[0],409)
  with b.db() as c:r=c.execute('SELECT * FROM deliveries').fetchone()
  self.assertEqual(r['transcript'],'Please bring a clean blanket.')
  self.assertEqual(self.post('/api/deliveries/'+result[1]['delivery_id']+'/acknowledge')[0],200)
 def test_failed_transcription_not_delivered(self):
  with patch.object(b,'transcribe',side_effect=RuntimeError('Local model unavailable')):
   self.assertEqual(self.upload()[0],202);b.process_next()
  with b.db() as c:
   row=c.execute('SELECT state,audio FROM requests WHERE id=8').fetchone()
  self.assertEqual(row['state'],'Retry required');self.assertTrue((b.DATA/row['audio']).exists())
  self.assertEqual(self.dispatch()[0],409)
 def test_patient_request_recording(self):
  created=self.post('/api/requests',json.dumps({'room':'412 · B'}).encode())
  self.assertEqual(created[0],201);rid=created[1]['request_id']
  second=self.post('/api/requests',json.dumps({'room':'412 · B'}).encode())
  self.assertNotEqual(rid,second[1]['request_id'])
  with patch.object(b,'transcribe',return_value={'text':'Please help me sit up.','language':'eng'}):
   result=self.post(f'/api/requests/{rid}/audio',b'RIFF0000WAVEfixture');b.process_next()
  self.assertEqual(result[0],202)
  with b.db() as c:row=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
  self.assertEqual(row['room'],'412 · B');self.assertEqual(row['transcript'],'Please help me sit up.')
 def test_patient_request_validation(self):
  self.assertEqual(self.post('/api/requests',b'{}')[0],400)
  self.assertEqual(self.post('/api/requests',b'{"room":"412"}',token=False)[0],401)
 def test_typed_request_feedback_pipeline(self):
  created=self.post('/api/requests',json.dumps({'room':'412 · B','text':'  Water please  '}).encode())
  self.assertEqual(created[0],201);rid=created[1]['request_id']
  self.assertEqual(created[1]['transcript'],'Water please')
  self.assertEqual(created[1]['state'],'Ready to send')
  with b.db() as c:
   row=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
  self.assertEqual(row['transcript'],'Water please');self.assertEqual(row['room'],'412 · B')
  feedback=json.dumps({'rating':4,'resolution_answer':'partly','comment':'Thank you'}).encode()
  self.assertEqual(self.post(f'/api/requests/{rid}/feedback',feedback)[0],201)
  self.assertEqual(self.post(f'/api/requests/{rid}/feedback',feedback)[0],409)
  with b.db() as c:
   saved=c.execute('SELECT * FROM feedback WHERE request_id=?',(rid,)).fetchone()
  self.assertEqual(saved['rating'],4);self.assertEqual(saved['resolution_answer'],'partly')
 def test_typed_request_validation(self):
  for text in ['', '   ', 123, 'x'*4001]:
   self.assertEqual(self.post('/api/requests',json.dumps({'room':'412','text':text}).encode())[0],400)
 def test_queue_bound_and_restart(self):
  ids=[]
  for i in range(5):
   rid=self.post('/api/requests',json.dumps({'room':str(i)}).encode())[1]['request_id'];ids.append(rid)
   self.assertEqual(self.post(f'/api/requests/{rid}/audio',b'RIFF0000WAVEfixture')[0],202 if i<4 else 429)
  b.initialize()
  with b.db() as c:
   row=c.execute('SELECT state,error FROM requests WHERE id=?',(ids[0],)).fetchone()
  self.assertEqual(row['state'],'Retry required');self.assertIn('interrupted',row['error'])
 def test_invalid_audio(self):
  self.assertEqual(self.post('/api/requests/8/audio',b'not audio')[0],400)
if __name__=='__main__':unittest.main()
