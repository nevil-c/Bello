"""Optional long-term archive: copies request records from the local SQLite database to the Bello AWS API.

Off unless BELLO_CLOUD_URL is set. The Pi stays the source of truth; a failed or slow
upload never blocks patients or staff and is retried later. Only text records are
sent (never audio). Needs only the standard library.

BELLO_CLOUD_URL      https://<id>.execute-api.<region>.amazonaws.com/prod
BELLO_CLOUD_API_KEY  API Gateway key from the deployed stack
BELLO_SITE_ID        short name for this unit or device (default: default)
"""
import json
import os
import re
import time
import urllib.error
import urllib.request

INTERVAL=15
MAX_BACKOFF=300

def config():
    url=os.environ.get('BELLO_CLOUD_URL','').rstrip('/')
    if not url:return None
    key=os.environ.get('BELLO_CLOUD_API_KEY','')
    site=os.environ.get('BELLO_SITE_ID','default')
    if not key:raise SystemExit('BELLO_CLOUD_URL is set but BELLO_CLOUD_API_KEY is missing')
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',site):raise SystemExit('BELLO_SITE_ID may use letters, digits, - and _ (maximum 40)')
    local=re.match(r'http://(127\.0\.0\.1|localhost)(:|/|$)',url)
    if not url.startswith('https://') and not local:raise SystemExit('BELLO_CLOUD_URL must use https')
    return {'url':url,'key':key,'site':site}

def ensure_schema(c):
    c.execute('CREATE TABLE IF NOT EXISTS cloud_sync(request_id INTEGER PRIMARY KEY,version REAL NOT NULL,synced REAL NOT NULL)')

def pending(c):
    """Requests with activity newer than the last archived version, oldest first.
    Sample requests with no history, and demo rows (backend.py --seed-demo), are never sent."""
    return c.execute('''SELECT r.id,MAX(h.created) AS version FROM requests r JOIN history h ON h.request_id=r.id
LEFT JOIN cloud_sync s ON s.request_id=r.id WHERE COALESCE(r.sample_text,'')!='Demo request'
GROUP BY r.id HAVING s.version IS NULL OR MAX(h.created)>s.version ORDER BY r.id''').fetchall()

def record(c,rid,version):
    r=dict(c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone())
    feedback=c.execute('SELECT rating,resolution_answer,comment,created FROM feedback WHERE request_id=?',(rid,)).fetchone()
    more_info=r.get('more_info_needed')
    return {'request_id':r['id'],'version':version,'room':r['room'],'recommended':r['recommended'],'transcript':r['transcript'],'language':r['language'],'state':r['state'],
        'transcript_en':r.get('transcript_en'),'ui_language':r.get('ui_language'),'category':r.get('category'),'urgency':r.get('urgency'),
        'urgency_confidence':r.get('urgency_confidence'),'more_info_needed':None if more_info is None else bool(more_info),
        'review':r.get('review'),'created':r.get('created'),'resolved':r.get('resolved'),'called':r.get('called'),
        'deliveries':[dict(d) for d in c.execute('SELECT id,department,status,created,transcript FROM deliveries WHERE request_id=? ORDER BY created',(rid,))],
        'feedback':dict(feedback) if feedback else None,
        'history':[dict(h) for h in c.execute('SELECT event,created FROM history WHERE request_id=? ORDER BY id',(rid,))]}

def upload(cfg,rec):
    request=urllib.request.Request(f"{cfg['url']}/sites/{cfg['site']}/requests/{rec['request_id']}",data=json.dumps(rec).encode(),method='PUT',headers={'Content-Type':'application/json','x-api-key':cfg['key']})
    with urllib.request.urlopen(request,timeout=10) as response:
        return response.status

def sync_once(connect,cfg):
    """Upload everything pending. Stops at the first failure so the next pass retries in order.
    Returns (uploaded, ok)."""
    with connect() as c:todo=[(row['id'],row['version']) for row in pending(c)]
    sent=0
    for rid,version in todo:
        with connect() as c:rec=record(c,rid,version)
        try:upload(cfg,rec)
        except urllib.error.HTTPError as error:
            # A snapshot the archive refuses as invalid would block the queue forever; record it as skipped (synced=0) and move on.
            print(f'Cloud archive rejected request {rid}: HTTP {error.code}',flush=True)
            if error.code in (400,413):
                with connect() as c:c.execute('INSERT OR REPLACE INTO cloud_sync VALUES(?,?,?)',(rid,version,0))
                continue
            return sent,False
        except (urllib.error.URLError,OSError,TimeoutError) as error:
            print(f'Cloud archive unreachable ({type(error).__name__}); will retry',flush=True)
            return sent,False
        with connect() as c:c.execute('INSERT OR REPLACE INTO cloud_sync VALUES(?,?,?)',(rid,version,time.time()))
        sent+=1
    return sent,True

def worker(stop,connect,cfg):
    delay=INTERVAL
    while not stop.is_set():
        try:sent,ok=sync_once(connect,cfg)
        except Exception as error:
            print(f'Cloud archive error: {type(error).__name__}',flush=True);sent,ok=0,False
        delay=INTERVAL if ok else min(delay*2,MAX_BACKOFF)
        stop.wait(delay)
