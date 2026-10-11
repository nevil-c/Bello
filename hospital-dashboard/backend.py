"""Bello: audio -> ElevenLabs transcript -> shared department queue.
Run python3 backend.py. No third-party Python dependencies required.
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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('BELLO_DATA_DIR',str(ROOT/'transcript-data')))
TOKEN=os.environ.get('BELLO_API_TOKEN') or secrets.token_urlsafe(32)
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
        c.execute("UPDATE requests SET state='Retry required' WHERE state='Transcribing'")

def event(c,rid,text):c.execute('INSERT INTO history(request_id,event,created) VALUES(?,?,?)',(rid,text,time.time()))

def transcribe(audio,extension):
    key=os.environ.get('ELEVENLABS_API_KEY','')
    if not key:raise RuntimeError('Set ELEVENLABS_API_KEY on the server first.')
    boundary='bello-'+secrets.token_hex(16)
    fields={'model_id':'scribe_v2','tag_audio_events':'false','diarize':'false'}
    parts=[]
    for name,value in fields.items():parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="request.{extension}"\r\nContent-Type: {"audio/wav" if extension=="wav" else "audio/mpeg"}\r\n\r\n'.encode()+audio+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    request=Request('https://api.elevenlabs.io/v1/speech-to-text',data=b''.join(parts),headers={'xi-api-key':key,'Content-Type':f'multipart/form-data; boundary={boundary}'})
    try:
        with urlopen(request,timeout=120) as response:result=json.load(response)
    except HTTPError as error:raise RuntimeError(f'ElevenLabs returned HTTP {error.code}. Check credentials, quota, and audio format.') from None
    except (URLError,TimeoutError):raise RuntimeError('ElevenLabs could not be reached. Retry may incur another transcription charge.') from None
    text=result.get('text','').strip()
    if not text:raise RuntimeError('No speech transcript returned. Nothing was sent to a department.')
    return {'text':text,'language':result.get('language_code','unknown')}

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
        with db() as c:
            if path=='/api/requests':return self.send(200,{'requests':[dict(r) for r in c.execute('SELECT id,room,recommended,transcript,language,state FROM requests')]})
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
            if not os.environ.get('ELEVENLABS_API_KEY'):return self.send(503,{'error':'Set ELEVENLABS_API_KEY on the server first'})
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
                if current['state']=='Transcribing' or current['transcript'] or c.execute('SELECT 1 FROM deliveries WHERE request_id=?',(rid,)).fetchone():return self.send(409,{'error':'Already transcribing, transcribed, or dispatched. No duplicate API request made.'})
                ext='wav' if wav else 'mp3';name=f'{rid}-{secrets.token_hex(8)}.{ext}';(DATA/name).write_bytes(body)
                c.execute("UPDATE requests SET audio=?,state='Transcribing' WHERE id=?",(name,rid));event(c,rid,'Recording received; transcription started')
            try:result=transcribe(body,ext)
            except (RuntimeError,ValueError) as e:
                with db() as c:c.execute("UPDATE requests SET state='Retry required' WHERE id=?",(rid,));event(c,rid,'Transcription failed; no delivery created')
                return self.send(502,{'error':str(e)})
            with db() as c:
                c.execute("UPDATE requests SET transcript=?,language=?,state='Ready to send' WHERE id=?",(result['text'],result['language'],rid));event(c,rid,'Transcript saved from ElevenLabs')
            return self.send(201,{'request_id':rid,'transcript':result['text'],'language':result['language'],'state':'Ready to send'})
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
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8766);args=parser.parse_args();initialize()
    print(f'Bello: http://127.0.0.1:{args.port}/ | Department inbox: /departments',flush=True)
    print('Private API token: '+TOKEN,flush=True)
    with ThreadingHTTPServer(('127.0.0.1',args.port),Handler) as server:
        try:server.serve_forever()
        except KeyboardInterrupt:pass
if __name__=='__main__':main()
