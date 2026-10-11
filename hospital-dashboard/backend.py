"""Bello: audio -> local faster-whisper transcript -> shared department queue.
Run python3 backend.py. Install requirements.txt and run prepare_model.py first.
"""
import argparse
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import threading
from local_stt import load_model, transcribe
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('BELLO_DATA_DIR',str(ROOT/'transcript-data')))
TOKEN=os.environ.get('BELLO_API_TOKEN') or secrets.token_urlsafe(32)
PREVIEWS={}
PREVIEW_LOCK=threading.Lock()

TEAMS=['Primary Nurse','Housekeeping & Facilities','Food Services','Unit Clerk / IT Maintenance']

class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def db():
    DATA.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DATA/'bello.sqlite3',timeout=10,factory=Connection);c.row_factory=sqlite3.Row;return c

def initialize():
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,room TEXT,recommended TEXT,sample_text TEXT,transcript TEXT,language TEXT,state TEXT DEFAULT 'Awaiting recording',audio TEXT);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,request_id INTEGER UNIQUE,department TEXT,transcript TEXT,room TEXT,language TEXT,status TEXT,created REAL);
CREATE TABLE IF NOT EXISTS feedback(request_id INTEGER PRIMARY KEY,rating INTEGER NOT NULL,resolution_answer TEXT NOT NULL,comment TEXT,created REAL);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,request_id INTEGER,event TEXT,created REAL);''')
        for rid,text,room in re.findall(r"\{id:(\d+),request:'(.*?)',urgency:\d+,room:'(.*?)'",(ROOT/'dist/index.html').read_text()):
            team={6:TEAMS[1],8:TEAMS[1],9:TEAMS[3],10:TEAMS[2]}.get(int(rid),TEAMS[0])
            c.execute('INSERT OR IGNORE INTO requests(id,room,recommended,sample_text) VALUES(?,?,?,?)',(int(rid),room,team,text))
        columns={row[1] for row in c.execute('PRAGMA table_info(requests)')}
        if 'error' not in columns:c.execute('ALTER TABLE requests ADD COLUMN error TEXT')
        interrupted=list(c.execute("SELECT id FROM requests WHERE state IN ('Queued','Transcribing')"))
        for row in interrupted:
            c.execute("UPDATE requests SET state='Retry required',error='Processing interrupted. Recording retained; retry explicitly.' WHERE id=?",(row['id'],))
            event(c,row['id'],'Local transcription interrupted; recording retained')

def event(c,rid,text):c.execute('INSERT INTO history(request_id,event,created) VALUES(?,?,?)',(rid,text,time.time()))

def process_next():
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM requests WHERE state='Queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:return False
        c.execute("UPDATE requests SET state='Transcribing' WHERE id=?",(row['id'],))
        event(c,row['id'],'Local transcription started')
    try:
        path=DATA/row['audio']
        result=transcribe(path.read_bytes(),path.suffix.lstrip('.'))
        with db() as c:
            c.execute("UPDATE requests SET transcript=?,language=?,state='Ready to send',error=NULL WHERE id=?",(result['text'],result['language'],row['id']))
            event(c,row['id'],'Transcript saved from local faster-whisper')
    except Exception as error:
        failure='No speech detected. No transcript was saved.' if str(error)=='No speech detected. No transcript was saved.' else 'Local transcription failed. Recording retained; retry explicitly.'
        with db() as c:
            c.execute("UPDATE requests SET state='Retry required',error=? WHERE id=?",(failure,row['id']))
            event(c,row['id'],'Local transcription failed; no delivery created')
        print(f"Local transcription failed for request {row['id']}: {type(error).__name__}",flush=True)
    return True

def process_preview():
    # The same worker/model handles finals first; retain only the latest snapshot.
    with PREVIEW_LOCK:
        now=time.monotonic()
        for rid in list(PREVIEWS):
            if now-PREVIEWS[rid]['updated']>60:del PREVIEWS[rid]
        pending=next(((rid,p) for rid,p in PREVIEWS.items() if p['audio'] is not None),None)
        if pending is None:return False
        rid,preview=pending;audio=preview['audio'];preview['audio']=None
    try:text=transcribe(audio,'wav')['text']
    except Exception:text=''
    with PREVIEW_LOCK:
        if PREVIEWS.get(rid) is preview and text:preview['text']=text
    return True

def worker(stop):
    while not stop.is_set():
        if not process_next() and not process_preview():stop.wait(.1)

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def send(self,code,data,kind='application/json'):
        body=json.dumps(data).encode() if kind=='application/json' else data.encode()
        self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
    def authorized(self):return hmac.compare_digest(self.headers.get('X-Bello-Token',''),TOKEN)
    def do_GET(self):
        path=urlsplit(self.path).path
        if path in ('/','/departments'):
            if self.headers.get('Host','').split(':')[0] not in ('127.0.0.1','localhost'):return self.send(403,{'error':'Local demo only'})
            file=ROOT/('dist/index.html' if path=='/' else 'dist/departments.html')
            html=file.read_text().replace('</head>','<script>window.BELLO_TOKEN='+json.dumps(TOKEN)+';</script></head>')
            return self.send(200,html,'text/html; charset=utf-8')
        if not self.authorized():return self.send(401,{'error':'API token required'})
        preview=re.fullmatch(r'/api/requests/(\d+)/preview',path)
        if preview:
            with PREVIEW_LOCK:text=PREVIEWS.get(int(preview[1]),{}).get('text','')
            return self.send(200,{'text':text,'provisional':True})
        with db() as c:
            if path=='/api/requests':return self.send(200,{'requests':[dict(r) for r in c.execute('SELECT id,room,recommended,transcript,language,state,error FROM requests')]})
            status=re.fullmatch(r'/api/requests/(\d+)',path)
            if status:
                row=c.execute('SELECT id AS request_id,room,transcript,language,state,error FROM requests WHERE id=?',(int(status[1]),)).fetchone()
                return self.send(200,dict(row)) if row else self.send(404,{'error':'Unknown request'})
            if path=='/api/departments/queue':
                department=parse_qs(urlsplit(self.path).query).get('department',[''])[0]
                if department not in TEAMS:return self.send(400,{'error':'Select a recognized department'})
                return self.send(200,{'deliveries':[dict(r) for r in c.execute('SELECT * FROM deliveries WHERE department=? ORDER BY created',(department,))]})
            m=re.fullmatch(r'/api/requests/(\d+)/history',path)
            if m:return self.send(200,{'history':[dict(r) for r in c.execute('SELECT event,created FROM history WHERE request_id=? ORDER BY id',(int(m[1]),))]})
        return self.send(404,{'error':'Not found'})
    def do_POST(self):
        if not self.authorized():return self.send(401,{'error':'API token required'})
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:return self.send(400,{'error':'Invalid body length'})
        if not 0<=length<=10*1024*1024:return self.send(413,{'error':'Maximum recording size is 10 MB'})
        body=self.rfile.read(length);path=urlsplit(self.path).path
        if path=='/api/requests':
            try:payload=json.loads(body)
            except ValueError:return self.send(400,{'error':'JSON object required'})
            room=payload.get('room') if isinstance(payload,dict) else None
            if not isinstance(room,str) or not room.strip() or len(room)>50:return self.send(400,{'error':'Room is required (maximum 50 characters)'})
            empty=payload.get('confirmed_empty') is True
            text='[Assistance requested without a message — patient confirmed submission]' if empty else payload.get('text')
            if text is not None and (not isinstance(text,str) or not text.strip() or len(text)>4000):return self.send(400,{'error':'Text must contain 1–4000 characters'})
            with db() as c:
                cursor=c.execute('INSERT INTO requests(room,recommended,sample_text,transcript,state) VALUES(?,?,?,?,?)',(room.strip(),TEAMS[0],'Awaiting voice transcript',text.strip() if text is not None else None,'Ready to send' if text is not None else 'Awaiting recording'))
                rid=cursor.lastrowid;event(c,rid,'Patient request created; pending clerk review')
                if text is not None:event(c,rid,'Patient explicitly submitted an empty assistance request; no transcript' if empty else 'Typed message received and saved')
            return self.send(201,{'request_id':rid,'room':room.strip(),'state':'Ready to send' if text is not None else 'Awaiting recording','transcript':text.strip() if text is not None else None})
        preview=re.fullmatch(r'/api/requests/(\d+)/preview',path)
        if preview:
            rid=int(preview[1])
            if len(body)>1024*1024 or body[:4]!=b'RIFF' or body[8:12]!=b'WAVE':return self.send(400,{'error':'Send a WAV snapshot under 1 MB'})
            with db() as c:
                row=c.execute('SELECT state FROM requests WHERE id=?',(rid,)).fetchone()
                if not row or row['state']!='Awaiting recording':return self.send(409,{'error':'Recording already finalized or unknown'})
                with PREVIEW_LOCK:
                    now=time.monotonic()
                    for old in list(PREVIEWS):
                        if now-PREVIEWS[old]['updated']>60:del PREVIEWS[old]
                    if rid not in PREVIEWS and len(PREVIEWS)>=4:return self.send(429,{'error':'Live transcription busy'})
                    item=PREVIEWS.setdefault(rid,{'text':''})
                    item.update(audio=body,updated=now)
                    text=item['text']
            return self.send(202,{'text':text,'provisional':True})
        feedback=re.fullmatch(r'/api/requests/(\d+)/feedback',path)
        if feedback:
            rid=int(feedback[1])
            try:payload=json.loads(body)
            except ValueError:return self.send(400,{'error':'JSON object required'})
            if not isinstance(payload,dict):return self.send(400,{'error':'JSON object required'})
            rating=payload.get('rating');answer=payload.get('resolution_answer');comment=payload.get('comment','')
            if type(rating) is not int or rating not in range(1,6) or answer not in ('yes','partly','no') or not isinstance(comment,str) or len(comment)>2000:
                return self.send(400,{'error':'rating must be 1–5, resolution_answer yes/partly/no, comment at most 2000 characters'})
            with db() as c:
                if not c.execute('SELECT 1 FROM requests WHERE id=?',(rid,)).fetchone():return self.send(404,{'error':'Unknown request'})
                try:c.execute('INSERT INTO feedback VALUES(?,?,?,?,?)',(rid,rating,answer,comment.strip(),time.time()))
                except sqlite3.IntegrityError:return self.send(409,{'error':'Feedback already submitted for this request'})
                event(c,rid,'Patient satisfaction feedback received')
            return self.send(201,{'request_id':rid,'message':'Thank you for your feedback'})
        match=re.fullmatch(r'/api/requests/(\d+)/(audio|dispatch)',path)
        ack=re.fullmatch(r'/api/deliveries/([a-f0-9]{32})/acknowledge',path)
        if ack:
            with db() as c:
                r=c.execute('SELECT * FROM deliveries WHERE id=?',(ack[1],)).fetchone()
                if not r:return self.send(404,{'error':'Unknown delivery'})
                if r['status']!='Acknowledged':
                    c.execute("UPDATE deliveries SET status='Acknowledged' WHERE id=?",(ack[1],));event(c,r['request_id'],'Department acknowledged: '+r['department'])
            return self.send(200,{'status':'Acknowledged'})
        if not match:return self.send(404,{'error':'Not found'})
        rid=int(match[1])
        with db() as c:r=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
        if not r:return self.send(404,{'error':'Unknown request'})
        if match[2]=='audio':
            wav=body[:4]==b'RIFF' and body[8:12]==b'WAVE'
            mp3=body[:3]==b'ID3' or (len(body)>1 and body[0]==255 and body[1]&224==224)
            if not wav and not mp3:return self.send(400,{'error':'Send raw WAV or MP3 bytes'})
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
                if current['state'] in ('Queued','Transcribing') or current['transcript'] or c.execute('SELECT 1 FROM deliveries WHERE request_id=?',(rid,)).fetchone():return self.send(409,{'error':'Already transcribing, transcribed, or dispatched. No duplicate job created.'})
                if c.execute("SELECT COUNT(*) FROM requests WHERE state IN ('Queued','Transcribing')").fetchone()[0]>=4:return self.send(429,{'error':'Transcription queue full. Please wait before retrying.'})
                ext='wav' if wav else 'mp3';name=f'{rid}-{secrets.token_hex(8)}.{ext}';(DATA/name).write_bytes(body)
                c.execute("UPDATE requests SET audio=?,state='Queued',error=NULL WHERE id=?",(name,rid));event(c,rid,'Recording received; queued for local transcription')
            with PREVIEW_LOCK:PREVIEWS.pop(rid,None)
            return self.send(202,{'request_id':rid,'transcript':None,'language':None,'state':'Queued'})
        try:payload=json.loads(body)
        except ValueError:return self.send(400,{'error':'JSON object required'})
        team=payload.get('department') if isinstance(payload,dict) else None
        if team not in TEAMS:return self.send(400,{'error':'Choose one recognized department'})
        with db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
            if not r['transcript']:return self.send(409,{'error':'No saved transcript. Transcribe a recording first.'})
            if r['recommended']==TEAMS[0] and team!=TEAMS[0]:return self.send(409,{'error':'This seeded clinical example requires Primary Nurse review.'})
            did=secrets.token_hex(16)
            try:c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?)',(did,rid,team,r['transcript'],r['room'],r['language'],'Delivered to queue',time.time()))
            except sqlite3.IntegrityError:return self.send(409,{'error':'Transcript already delivered; duplicate prevented'})
            c.execute("UPDATE requests SET state='Delivered to queue' WHERE id=?",(rid,));event(c,rid,'Saved transcript delivered to '+team)
        return self.send(201,{'delivery_id':did,'department':team,'status':'Delivered to queue'})

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766);args=parser.parse_args()
    # A second server must not reset the first server's in-flight requests.
    import fcntl
    DATA.mkdir(parents=True,exist_ok=True)
    instance_lock=(DATA/'.backend.lock').open('a')
    try:fcntl.flock(instance_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('A backend is already using this data directory. Stop it before launching another.')
    load_model();initialize()
    print(f'Bello: http://127.0.0.1:{args.port}/ | Department inbox: /departments',flush=True)
    print('Private API token: '+TOKEN,flush=True)
    stop=threading.Event();thread=threading.Thread(target=worker,args=(stop,),daemon=True);thread.start()
    ThreadingHTTPServer.allow_reuse_address=True
    with ThreadingHTTPServer(('127.0.0.1',args.port),Handler) as server:
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:stop.set();thread.join(timeout=2)
if __name__=='__main__':main()
