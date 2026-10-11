import json,os,sqlite3,tempfile,threading,unittest
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from unittest.mock import patch
import aws_sync as s

class Archive(BaseHTTPRequestHandler):
    received=[];status=200
    def log_message(self,*args):pass
    def do_PUT(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        Archive.received.append((self.path,self.headers.get('x-api-key'),body))
        self.send_response(Archive.status);self.send_header('Content-Length','2');self.end_headers();self.wfile.write(b'{}')

class Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.path=Path(self.tmp.name)/'t.sqlite3'
        with self.connect() as c:
            c.executescript('''CREATE TABLE requests(id INTEGER PRIMARY KEY,room TEXT,recommended TEXT,sample_text TEXT,transcript TEXT,language TEXT,state TEXT,audio TEXT);
CREATE TABLE deliveries(id TEXT PRIMARY KEY,request_id INTEGER UNIQUE,department TEXT,transcript TEXT,room TEXT,language TEXT,status TEXT,created REAL);
CREATE TABLE feedback(request_id INTEGER PRIMARY KEY,rating INTEGER,resolution_answer TEXT,comment TEXT,created REAL);
CREATE TABLE history(id INTEGER PRIMARY KEY,request_id INTEGER,event TEXT,created REAL);''')
            c.execute("INSERT INTO requests VALUES(1,'412 B','Primary Nurse','sample','Please help me sit up.','eng','Delivered to queue','1-secret.wav')")
            c.execute("INSERT INTO requests VALUES(2,'Sample room','Primary Nurse','demo fixture',NULL,NULL,'Awaiting recording',NULL)")
            c.execute("INSERT INTO history(request_id,event,created) VALUES(1,'Patient request created',10.0)")
            c.execute("INSERT INTO deliveries VALUES('d1',1,'Primary Nurse','Please help me sit up.','412 B','eng','Acknowledged',11.0)")
            s.ensure_schema(c)
        Archive.received=[];Archive.status=200
        self.server=HTTPServer(('127.0.0.1',0),Archive);threading.Thread(target=self.server.serve_forever,daemon=True).start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.cfg={'url':f'http://127.0.0.1:{self.server.server_port}','key':'k3y','site':'unit-4'}
    def connect(self):
        c=sqlite3.connect(self.path,timeout=10);c.row_factory=sqlite3.Row
        class Closing:
            def __enter__(_):return c.__enter__()
            def __exit__(_,*a):
                try:return c.__exit__(*a)
                finally:c.close()
        return Closing()
    def test_uploads_record_without_audio_or_fixtures(self):
        self.assertEqual(s.sync_once(self.connect,self.cfg),(1,True))
        path,key,body=Archive.received[0]
        self.assertEqual(path,'/sites/unit-4/requests/1');self.assertEqual(key,'k3y')
        self.assertEqual(body['transcript'],'Please help me sit up.');self.assertEqual(body['deliveries'][0]['status'],'Acknowledged')
        self.assertEqual(body['version'],10.0);self.assertNotIn('1-secret.wav',json.dumps(body));self.assertEqual(len(Archive.received),1)
    def test_only_changes_are_resent(self):
        s.sync_once(self.connect,self.cfg);self.assertEqual(s.sync_once(self.connect,self.cfg),(0,True))
        with self.connect() as c:c.execute("INSERT INTO history(request_id,event,created) VALUES(1,'Department acknowledged',12.0)")
        self.assertEqual(s.sync_once(self.connect,self.cfg),(1,True));self.assertEqual(Archive.received[-1][2]['version'],12.0)
    def test_patient_feedback_reaches_the_archive(self):
        with self.connect() as c:
            c.execute("INSERT INTO feedback VALUES(1,5,'Just in time',NULL,20.0)")
            c.execute("INSERT INTO history(request_id,event,created) VALUES(1,'Patient satisfaction feedback received',20.0)")
        self.assertEqual(s.sync_once(self.connect,self.cfg),(1,True))
        body=Archive.received[-1][2]
        self.assertEqual(body['version'],20.0)
        self.assertEqual(body['feedback']['rating'],5);self.assertEqual(body['feedback']['resolution_answer'],'Just in time')
    def test_server_error_retries_later(self):
        Archive.status=500;self.assertEqual(s.sync_once(self.connect,self.cfg),(0,False))
        Archive.status=200;self.assertEqual(s.sync_once(self.connect,self.cfg),(1,True))
    def test_unreachable_archive_is_not_fatal(self):
        self.cfg['url']='http://127.0.0.1:9';self.assertEqual(s.sync_once(self.connect,self.cfg),(0,False))
        with self.connect() as c:self.assertEqual(len(s.pending(c)),1)
    def test_rejected_record_is_skipped_not_looped(self):
        Archive.status=400;self.assertEqual(s.sync_once(self.connect,self.cfg),(0,True))
        with self.connect() as c:self.assertEqual(len(s.pending(c)),0)
    def test_config(self):
        with patch.dict(os.environ,{},clear=True):self.assertIsNone(s.config())
        with patch.dict(os.environ,{'BELLO_CLOUD_URL':'https://x.example/prod'},clear=True):self.assertRaises(SystemExit,s.config)
        with patch.dict(os.environ,{'BELLO_CLOUD_URL':'http://x.example/prod','BELLO_CLOUD_API_KEY':'k'},clear=True):self.assertRaises(SystemExit,s.config)
        with patch.dict(os.environ,{'BELLO_CLOUD_URL':'https://x.example/prod/','BELLO_CLOUD_API_KEY':'k','BELLO_SITE_ID':'bad id'},clear=True):self.assertRaises(SystemExit,s.config)
        with patch.dict(os.environ,{'BELLO_CLOUD_URL':'https://x.example/prod/','BELLO_CLOUD_API_KEY':'k'},clear=True):self.assertEqual(s.config(),{'url':'https://x.example/prod','key':'k','site':'default'})

if __name__=='__main__':unittest.main()
