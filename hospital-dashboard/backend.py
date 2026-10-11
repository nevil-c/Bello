"""Bello LAN server: patient audio -> faster-whisper transcript -> English text -> Laya routing -> staff apps.
API only: the staff apps in ../staff-app and the bedside units in ../patient-app are its clients.
Run on the server: BELLO_API_TOKEN=... python3 backend.py --host 0.0.0.0
Install requirements.txt and run prepare_model.py (and prepare_translation_model.py) first. See TRANSCRIPT_SETUP.md.
"""
import argparse
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
import threading
from local_stt import load_model, transcribe, translate_to_english
import admin_stats
import aws_sync
import decision
import translation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs

ROOT=Path(__file__).resolve().parent
DATA=Path(os.environ.get('BELLO_DATA_DIR',str(ROOT/'transcript-data')))
TOKEN=os.environ.get('BELLO_API_TOKEN') or secrets.token_urlsafe(32)
# nllb: Whisper transcribes, NLLB translates the text. whisper: Whisper (full large-v3) translates the audio itself.
EN_ENGINE=os.environ.get('BELLO_EN_ENGINE','nllb')
PATIENT_APP=ROOT.parent/'patient-app'
LANGUAGES={code for code,_,_ in translation.LANGUAGES}
PREVIEWS={}
PREVIEW_LOCK=threading.Lock()
I18N_LOCK=threading.Lock()
CHANGES=threading.Condition()
VERSION=[0]
STOP=threading.Event()

TEAMS=['Primary Nurse','Housekeeping & Facilities','Food Services','Unit Clerk / IT Maintenance']
EMPTY_REQUEST='[Assistance requested without a message — patient confirmed submission]'
DEMO_MARK='Demo request'
COLUMNS={'error':'TEXT','transcript_en':'TEXT','ui_language':'TEXT','category':'TEXT','urgency':'INTEGER',
    'urgency_confidence':'REAL','more_info_needed':'INTEGER','model_scores':'TEXT','review':'TEXT',
    'created':'REAL','resolved':'REAL','called':'REAL'}
LIST_SQL='''SELECT r.id,r.room,r.recommended,r.transcript,r.transcript_en,r.language,r.ui_language,r.category,r.urgency,
r.urgency_confidence,r.more_info_needed,r.review,r.state,r.error,r.created,r.resolved,r.called,
d.department AS delivered_department,d.status AS delivery_status
FROM requests r LEFT JOIN deliveries d ON d.request_id=r.id ORDER BY r.id'''

class Connection(sqlite3.Connection):
    def __exit__(self,*args):
        try:return super().__exit__(*args)
        finally:self.close()

def db():
    DATA.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(DATA/'bello.sqlite3',timeout=10,factory=Connection);c.row_factory=sqlite3.Row;return c

def changed():
    """Wake the live dashboard streams. Call after the write has committed."""
    with CHANGES:
        VERSION[0]+=1;CHANGES.notify_all()

def initialize(seed_demo=False):
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,room TEXT,recommended TEXT,sample_text TEXT,transcript TEXT,language TEXT,state TEXT DEFAULT 'Awaiting recording',audio TEXT);
CREATE TABLE IF NOT EXISTS deliveries(id TEXT PRIMARY KEY,request_id INTEGER UNIQUE,department TEXT,transcript TEXT,room TEXT,language TEXT,status TEXT,created REAL);
CREATE TABLE IF NOT EXISTS feedback(request_id INTEGER PRIMARY KEY,rating INTEGER NOT NULL,resolution_answer TEXT NOT NULL,comment TEXT,created REAL);
CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY,request_id INTEGER,event TEXT,created REAL);''')
        columns={row[1] for row in c.execute('PRAGMA table_info(requests)')}
        for name,kind in COLUMNS.items():
            if name not in columns:c.execute(f'ALTER TABLE requests ADD COLUMN {name} {kind}')
        if seed_demo and not c.execute('SELECT 1 FROM requests LIMIT 1').fetchone():
            # Demo rows are typed requests, so they go through translation and routing like real ones.
            # They have no history and are marked, so the cloud archive never receives them.
            for sample in json.loads((ROOT/'demo_requests.json').read_text(encoding='utf-8')):
                c.execute("INSERT INTO requests(room,sample_text,transcript,language,ui_language,state,created) VALUES(?,?,?,?,?,'Classifying',?)",
                    (sample['room'],DEMO_MARK,sample['text'],sample.get('language','en'),sample.get('language','en'),time.time()))
        interrupted=list(c.execute("SELECT id FROM requests WHERE state IN ('Queued','Transcribing')"))
        for row in interrupted:
            c.execute("UPDATE requests SET state='Retry required',error='Processing interrupted. Recording retained; retry explicitly.' WHERE id=?",(row['id'],))
            event(c,row['id'],'Local transcription interrupted; recording retained')
        # Requests left in 'Classifying' are picked up again by the worker; routing is safe to repeat.

def event(c,rid,text):c.execute('INSERT INTO history(request_id,event,created) VALUES(?,?,?)',(rid,text,time.time()))

def process_next():
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT * FROM requests WHERE state='Queued' ORDER BY id LIMIT 1").fetchone()
        if row is None:return False
        c.execute("UPDATE requests SET state='Transcribing' WHERE id=?",(row['id'],))
        event(c,row['id'],'Local transcription started')
    changed()
    try:
        path=DATA/row['audio']
        result=transcribe(path.read_bytes(),path.suffix.lstrip('.'))
        # Saved before routing: the patient screen shows the original text as soon as it exists.
        with db() as c:
            c.execute("UPDATE requests SET transcript=?,language=?,state='Classifying',error=NULL WHERE id=?",(result['text'],result['language'],row['id']))
            event(c,row['id'],'Transcript saved from local faster-whisper')
    except Exception as error:
        failure='No speech detected. No transcript was saved.' if str(error)=='No speech detected. No transcript was saved.' else 'Local transcription failed. Recording retained; retry explicitly.'
        with db() as c:
            c.execute("UPDATE requests SET state='Retry required',error=? WHERE id=?",(failure,row['id']))
            event(c,row['id'],'Local transcription failed; no delivery created')
        print(f"Local transcription failed for request {row['id']}: {type(error).__name__}",flush=True)
    changed()
    return True

def english(row):
    language=row['language'] or 'en'
    if language=='en':return row['transcript']
    if EN_ENGINE=='whisper' and row['audio']:
        return translate_to_english((DATA/row['audio']).read_bytes(),language)
    return translation.to_english(row['transcript'],language)

def enrich(row):
    """English text and the decision model's routing. Never raises: a failure means clerk review."""
    result=dict(transcript_en=None,category=None,recommended=None,urgency=None,urgency_confidence=None,
        more_info_needed=None,model_scores=None,review=None)
    try:result['transcript_en']=english(row)
    except Exception as error:
        print(f"Translation failed for request {row['id']}: {type(error).__name__}: {error}",flush=True)
        result['review']='Translation unavailable. Read the original text.'
        return result,'Translation to English failed; clerk review needed'
    try:routing=decision.classify(result['transcript_en'])
    except Exception as error:
        print(f"Decision model failed for request {row['id']}: {type(error).__name__}: {error}",flush=True)
        result['review']='Routing model unavailable. Choose a department.'
        return result,'Decision model unavailable; clerk review needed'
    result.update(category=routing['category'],recommended=routing['department'],urgency=routing['urgency'],
        urgency_confidence=routing['urgency_confidence'],more_info_needed=int(routing['more_info_needed']),
        model_scores=json.dumps(routing['scores'],default=float))
    if routing['more_info_needed']:result['review']='Model suggests asking the patient for more detail.'
    return result,f"Decision model: {routing['department']}, urgency {routing['urgency']}/10, {routing['category']}"

def process_enrich():
    with db() as c:
        row=c.execute("SELECT * FROM requests WHERE state='Classifying' ORDER BY id LIMIT 1").fetchone()
    if row is None:return False
    result,note=enrich(row)
    with db() as c:
        c.execute('''UPDATE requests SET transcript_en=?,category=?,recommended=?,urgency=?,urgency_confidence=?,more_info_needed=?,
model_scores=?,review=?,state='Ready to send' WHERE id=? AND state='Classifying' ''',
            (result['transcript_en'],result['category'],result['recommended'],result['urgency'],result['urgency_confidence'],
             result['more_info_needed'],result['model_scores'],result['review'],row['id']))
        if row['sample_text']!=DEMO_MARK:event(c,row['id'],note)
    changed()
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
        if not process_next() and not process_enrich() and not process_preview():stop.wait(.1)

def requests_list(c):
    return [dict(r) for r in c.execute(LIST_SQL)]

def phone_book():
    try:return json.loads((ROOT/'departments.json').read_text(encoding='utf-8'))
    except (OSError,ValueError):return {}

def ui_strings(language):
    """Patient screen strings in one language: a built-in table if one is committed (Cantonese), else a cached
    machine translation. Missing keys fall back to English on the bedside unit."""
    source=json.loads((PATIENT_APP/'strings_en.json').read_text(encoding='utf-8'))
    if language=='en':return {'language':'en','strings':source,'source':'english'}
    reviewed=PATIENT_APP/f'strings_{language}.json'
    if reviewed.is_file():return {'language':language,'strings':json.loads(reviewed.read_text(encoding='utf-8')),'source':'built-in table'}
    digest=hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()[:16]
    cache=DATA/'i18n'/f'{language}.json'
    with I18N_LOCK:
        if cache.is_file():
            saved=json.loads(cache.read_text(encoding='utf-8'))
            if saved.get('digest')==digest:return {'language':language,'strings':saved['strings'],'source':'machine translation (cached)'}
        started=time.monotonic()
        strings=translation.translate_strings(source,language)
        cache.parent.mkdir(parents=True,exist_ok=True)
        cache.write_text(json.dumps({'digest':digest,'strings':strings},ensure_ascii=False,indent=1),encoding='utf-8')
        print(f'Translated {len(strings)} UI strings to {language} in {time.monotonic()-started:.1f} s',flush=True)
    return {'language':language,'strings':strings,'source':'machine translation'}

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):pass
    def send(self,code,data,kind='application/json'):
        body=json.dumps(data,default=float).encode() if kind=='application/json' else data.encode()
        self.send_response(code);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
    def authorized(self):return hmac.compare_digest(self.headers.get('X-Bello-Token',''),TOKEN)
    def stream(self):
        """Server-sent events: the full request list whenever it changes."""
        self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers()
        last=None;seen=-1;quiet=time.monotonic()
        try:
            while not STOP.is_set():
                with CHANGES:
                    if VERSION[0]==seen:CHANGES.wait(timeout=2)
                    seen=VERSION[0]
                with db() as c:snapshot=json.dumps({'requests':requests_list(c)},default=float)
                if snapshot!=last:
                    self.wfile.write(b'data: '+snapshot.encode()+b'\n\n');self.wfile.flush();last=snapshot;quiet=time.monotonic()
                elif time.monotonic()-quiet>15:
                    self.wfile.write(b': keep-alive\n\n');self.wfile.flush();quiet=time.monotonic()
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
    def do_GET(self):
        url=urlsplit(self.path);path=url.path;query=parse_qs(url.query)
        if not self.authorized():return self.send(401,{'error':'API token required'})
        if path=='/api/languages':return self.send(200,{'languages':translation.LANGUAGES})
        if path=='/api/stream':return self.stream()
        preview=re.fullmatch(r'/api/requests/(\d+)/preview',path)
        if preview:
            with PREVIEW_LOCK:text=PREVIEWS.get(int(preview[1]),{}).get('text','')
            return self.send(200,{'text':text,'provisional':True})
        i18n=re.fullmatch(r'/api/i18n/([a-z]{2,3})',path)
        if i18n:
            if i18n[1] not in LANGUAGES:return self.send(404,{'error':'Unknown language'})
            try:return self.send(200,ui_strings(i18n[1]))
            except translation.Unsupported as error:return self.send(404,{'error':str(error)})
            except Exception as error:
                print(f'UI translation failed for {i18n[1]}: {type(error).__name__}: {error}',flush=True)
                return self.send(503,{'error':'Translation unavailable; showing English'})
        if path=='/api/admin/stats':
            try:days=int(query.get('days',['7'])[0])
            except ValueError:days=0
            if days not in (1,7,30):return self.send(400,{'error':'days must be 1, 7 or 30'})
            cloud=aws_sync.config()
            if not cloud:return self.send(503,{'error':'Cloud archive is not configured on this server (set BELLO_CLOUD_URL).'})
            try:return self.send(200,admin_stats.stats(admin_stats.records(cloud),days,cloud['site']))
            except admin_stats.Unavailable as error:return self.send(502,{'error':str(error)})
        with db() as c:
            if path=='/api/requests':return self.send(200,{'requests':requests_list(c)})
            status=re.fullmatch(r'/api/requests/(\d+)',path)
            if status:
                row=c.execute('SELECT id AS request_id,room,transcript,language,state,error FROM requests WHERE id=?',(int(status[1]),)).fetchone()
                return self.send(200,dict(row)) if row else self.send(404,{'error':'Unknown request'})
            if path=='/api/rooms/open':
                room=query.get('room',[''])[0]
                count=c.execute('SELECT COUNT(*) FROM requests WHERE room=? AND resolved IS NULL AND transcript IS NOT NULL',(room,)).fetchone()[0]
                return self.send(200,{'room':room,'open':count})
            if path=='/api/departments/queue':
                department=query.get('department',[''])[0]
                if department not in TEAMS:return self.send(400,{'error':'Select a recognized department'})
                return self.send(200,{'deliveries':[dict(r) for r in c.execute('SELECT * FROM deliveries WHERE department=? ORDER BY created',(department,))]})
            m=re.fullmatch(r'/api/requests/(\d+)/history',path)
            if m:return self.send(200,{'history':[dict(r) for r in c.execute('SELECT event,created FROM history WHERE request_id=? ORDER BY id',(int(m[1]),))]})
        return self.send(404,{'error':'Not found'})
    def json_body(self,body):
        try:payload=json.loads(body)
        except ValueError:return None
        return payload if isinstance(payload,dict) else None
    def do_POST(self):
        if not self.authorized():return self.send(401,{'error':'API token required'})
        try:length=int(self.headers.get('Content-Length','0'))
        except ValueError:return self.send(400,{'error':'Invalid body length'})
        if not 0<=length<=10*1024*1024:return self.send(413,{'error':'Maximum recording size is 10 MB'})
        body=self.rfile.read(length);path=urlsplit(self.path).path
        if path=='/api/requests':
            payload=self.json_body(body)
            if payload is None:return self.send(400,{'error':'JSON object required'})
            room=payload.get('room')
            if not isinstance(room,str) or not room.strip() or len(room)>50:return self.send(400,{'error':'Room is required (maximum 50 characters)'})
            ui_language=payload.get('ui_language','en')
            if ui_language not in LANGUAGES:return self.send(400,{'error':'ui_language must be a supported language code'})
            empty=payload.get('confirmed_empty') is True
            text=EMPTY_REQUEST if empty else payload.get('text')
            if text is not None and (not isinstance(text,str) or not text.strip() or len(text)>4000):return self.send(400,{'error':'Text must contain 1–4000 characters'})
            text=text.strip() if text is not None else None
            # Typed text uses the screen's language, except plain ASCII, which the on-screen QWERTY keyboard produces.
            language=None if text is None else 'en' if empty or text.isascii() else ui_language
            state='Awaiting recording' if text is None else 'Ready to send' if empty else 'Classifying'
            review='Empty request. Ask the patient what they need.' if empty else None
            with db() as c:
                cursor=c.execute('INSERT INTO requests(room,sample_text,transcript,language,ui_language,state,review,created) VALUES(?,?,?,?,?,?,?,?)',
                    (room.strip(),'Awaiting voice transcript',text,language,ui_language,state,review,time.time()))
                rid=cursor.lastrowid;event(c,rid,'Patient request created')
                if text is not None:event(c,rid,'Patient explicitly submitted an empty assistance request; no transcript' if empty else 'Typed message received and saved')
            changed()
            return self.send(201,{'request_id':rid,'room':room.strip(),'state':state,'transcript':text})
        if path=='/api/rooms/resolve':
            payload=self.json_body(body)
            room=payload.get('room') if payload else None
            if not isinstance(room,str) or not room.strip() or len(room)>50:return self.send(400,{'error':'Room is required (maximum 50 characters)'})
            now=time.time()
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                ids=[r['id'] for r in c.execute('SELECT id FROM requests WHERE room=? AND resolved IS NULL AND transcript IS NOT NULL',(room.strip(),))]
                for rid in ids:
                    c.execute('UPDATE requests SET resolved=? WHERE id=?',(now,rid));event(c,rid,'Resolved at the bedside by staff')
            changed()
            return self.send(200,{'room':room.strip(),'resolved':len(ids)})
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
            payload=self.json_body(body)
            if payload is None:return self.send(400,{'error':'JSON object required'})
            rating=payload.get('rating');answer=payload.get('resolution_answer');comment=payload.get('comment','')
            if type(rating) is not int or rating not in range(1,6) or answer not in ('yes','partly','no') or not isinstance(comment,str) or len(comment)>2000:
                return self.send(400,{'error':'rating must be 1–5, resolution_answer yes/partly/no, comment at most 2000 characters'})
            with db() as c:
                if not c.execute('SELECT 1 FROM requests WHERE id=?',(rid,)).fetchone():return self.send(404,{'error':'Unknown request'})
                try:c.execute('INSERT INTO feedback VALUES(?,?,?,?,?)',(rid,rating,answer,comment.strip(),time.time()))
                except sqlite3.IntegrityError:return self.send(409,{'error':'Feedback already submitted for this request'})
                event(c,rid,'Patient satisfaction feedback received')
            changed()
            return self.send(201,{'request_id':rid,'message':'Thank you for your feedback'})
        match=re.fullmatch(r'/api/requests/(\d+)/(audio|dispatch)',path)
        ack=re.fullmatch(r'/api/deliveries/([a-f0-9]{32})/acknowledge',path)
        if ack:
            with db() as c:
                r=c.execute('SELECT * FROM deliveries WHERE id=?',(ack[1],)).fetchone()
                if not r:return self.send(404,{'error':'Unknown delivery'})
                if r['status']!='Acknowledged':
                    c.execute("UPDATE deliveries SET status='Acknowledged' WHERE id=?",(ack[1],));event(c,r['request_id'],'Department acknowledged: '+r['department'])
            changed()
            return self.send(200,{'status':'Acknowledged'})
        if not match:return self.send(404,{'error':'Not found'})
        rid=int(match[1])
        with db() as c:r=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
        if not r:return self.send(404,{'error':'Unknown request'})
        if match[2]=='audio':
            wav=body[:4]==b'RIFF' and body[8:12]==b'WAVE'
            mp3=body[:3]==b'ID3' or (len(body)>1 and body[0]==255 and body[1]&224==224)
            ogg=body[:4]==b'OggS'   # Opus-compressed recordings from bedside units on Bluetooth
            if not wav and not mp3 and not ogg:return self.send(400,{'error':'Send raw WAV, MP3 or Ogg bytes'})
            with db() as c:
                c.execute('BEGIN IMMEDIATE')
                current=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
                if current['state'] in ('Queued','Transcribing') or current['transcript'] or c.execute('SELECT 1 FROM deliveries WHERE request_id=?',(rid,)).fetchone():return self.send(409,{'error':'Already transcribing, transcribed, or dispatched. No duplicate job created.'})
                if c.execute("SELECT COUNT(*) FROM requests WHERE state IN ('Queued','Transcribing')").fetchone()[0]>=4:return self.send(429,{'error':'Transcription queue full. Please wait before retrying.'})
                ext='wav' if wav else 'ogg' if ogg else 'mp3';name=f'{rid}-{secrets.token_hex(8)}.{ext}';(DATA/name).write_bytes(body)
                c.execute("UPDATE requests SET audio=?,state='Queued',error=NULL WHERE id=?",(name,rid));event(c,rid,'Recording received; queued for local transcription')
            with PREVIEW_LOCK:PREVIEWS.pop(rid,None)
            changed()
            return self.send(202,{'request_id':rid,'transcript':None,'language':None,'state':'Queued'})
        payload=self.json_body(body)
        team=payload.get('department') if payload else None
        call=payload.get('call',False) if payload else False
        if team not in TEAMS:return self.send(400,{'error':'Choose one recognized department'})
        if not isinstance(call,bool):return self.send(400,{'error':'call must be true or false'})
        with db() as c:
            c.execute('BEGIN IMMEDIATE');r=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
            if not r['transcript']:return self.send(409,{'error':'No saved transcript. Transcribe a recording first.'})
            did=secrets.token_hex(16);now=time.time()
            # Departments read English; the original is kept on the request.
            text=r['transcript_en'] or r['transcript']
            try:c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?,?,?)',(did,rid,team,text,r['room'],r['language'],'Delivered to queue',now))
            except sqlite3.IntegrityError:return self.send(409,{'error':'Transcript already delivered; duplicate prevented'})
            c.execute("UPDATE requests SET state='Delivered to queue' WHERE id=?",(rid,));event(c,rid,'Saved transcript delivered to '+team)
            extension=phone_book().get(team,{}).get('phone')
            if call:
                # Placeholder for the automated phone call; nothing is dialled yet.
                c.execute('UPDATE requests SET called=? WHERE id=?',(now,rid))
                event(c,rid,f"Call placed to {team} ({'ext '+extension if extension else 'no number configured'}; stub, no call made)")
        changed()
        return self.send(201,{'delivery_id':did,'department':team,'status':'Delivered to queue','called':call,'phone':extension})

def load_optional(name,load):
    try:load();print(f'{name}: loaded',flush=True)
    except Exception as error:print(f'{name}: not available ({error}). Requests will need clerk review.',flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--host',default='127.0.0.1',help='0.0.0.0 to accept bedside units and dashboards on the LAN')
    parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--seed-demo',action='store_true',help='add demo_requests.json to an empty database')
    args=parser.parse_args()
    try:loopback=args.host=='localhost' or ipaddress.ip_address(args.host).is_loopback
    except ValueError:loopback=False  # a host name such as bello-server.local
    if not loopback and not os.environ.get('BELLO_API_TOKEN'):
        raise SystemExit('Set BELLO_API_TOKEN (the shared token the bedside units and dashboards use) before serving on the network.')
    if EN_ENGINE not in ('nllb','whisper'):raise SystemExit('BELLO_EN_ENGINE must be nllb or whisper')
    DATA.mkdir(parents=True,exist_ok=True)
    # A second server must not reset the first server's in-flight requests.
    instance_lock=(DATA/'.backend.lock').open('a')
    try:
        import fcntl
    except ImportError:
        print('Warning: no process lock on this platform; run only one backend per data directory.',flush=True)
    else:
        try:fcntl.flock(instance_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('A backend is already using this data directory. Stop it before launching another.')
    load_model();initialize(args.seed_demo)
    load_optional('Decision model',decision.load_model)
    if EN_ENGINE=='nllb':load_optional('Translation model',translation.load_model)
    print(f'English for routing: {EN_ENGINE}',flush=True)
    shown='127.0.0.1' if loopback else args.host
    print(f'Bello server: http://{shown}:{args.port} (staff apps: staff-app/clerk.py, inbox.py, admin.py)',flush=True)
    if not os.environ.get('BELLO_API_TOKEN'):print('Private API token: '+TOKEN,flush=True)
    thread=threading.Thread(target=worker,args=(STOP,),daemon=True);thread.start()
    cloud=aws_sync.config()
    if cloud:
        with db() as c:aws_sync.ensure_schema(c)
        threading.Thread(target=aws_sync.worker,args=(STOP,db,cloud),daemon=True).start()
        print('Cloud archive: on, site '+cloud['site'],flush=True)
    ThreadingHTTPServer.allow_reuse_address=True
    with ThreadingHTTPServer((args.host,args.port),Handler) as server:
        try:server.serve_forever()
        except KeyboardInterrupt:pass
        finally:STOP.set();thread.join(timeout=2)
if __name__=='__main__':main()
