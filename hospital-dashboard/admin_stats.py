"""Admin analytics from the AWS archive: most common issues, response times and routing quality.

Response time is request created -> staff pressed the bedside 'Staff: done' button (resolved).
Records come from the archive API (GET /sites/{site}/requests), cached briefly so the page can refresh often.
"""
from collections import Counter
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CACHE_SECONDS=60
MAX_PAGES=200
LONG_WAIT_MINUTES=15
_cache={'at':0.0,'key':None,'records':None}
_lock=threading.Lock()
STOPWORDS=set('''a an and are am as at be been but by can could do does for from get got have having he her
here him his how i i'm if in into is it it's its just me more my no not of on or our out please same she so some
someone than that the their them then there these they this to too up us very was we were what when where which who
why will with would you your can't don't im need needs want wants like feel feeling also again still bit little one
help could someone thank thanks hi hello getting another working anyone something come back over now'''.split())

class Unavailable(Exception):
    pass

def fetch(cfg):
    records=[];cursor=None
    for _ in range(MAX_PAGES):
        query={'limit':'100'}
        if cursor:query['cursor']=cursor
        request=urllib.request.Request(f"{cfg['url']}/sites/{cfg['site']}/requests?{urllib.parse.urlencode(query)}",headers={'x-api-key':cfg['key']})
        try:
            with urllib.request.urlopen(request,timeout=15) as response:page=json.load(response)
        except urllib.error.HTTPError as error:raise Unavailable(f'Cloud archive refused the request (HTTP {error.code}).') from None
        except (urllib.error.URLError,OSError,ValueError) as error:raise Unavailable(f'Cloud archive unreachable ({type(error).__name__}).') from None
        records.extend(page.get('requests',[]))
        cursor=page.get('next_cursor')
        if not cursor:return records
    return records

def records(cfg):
    key=(cfg['url'],cfg['site'])
    with _lock:
        if _cache['key']==key and _cache['records'] is not None and time.time()-_cache['at']<CACHE_SECONDS:return _cache['records']
        fetched=fetch(cfg)
        _cache.update(at=time.time(),key=key,records=fetched)
        return fetched

def created_at(record):
    if record.get('created') is not None:return float(record['created'])
    times=[float(h['created']) for h in record.get('history') or []]
    return min(times) if times else None

def minutes(record):
    start,end=created_at(record),record.get('resolved')
    if start is None or end is None or float(end)<start:return None
    return (float(end)-start)/60

def percentile(values,p):
    if not values:return None
    values=sorted(values);k=(len(values)-1)*p;lo=int(k);hi=min(lo+1,len(values)-1)
    return round(values[lo]+(values[hi]-values[lo])*(k-lo),1)

def department(record):
    deliveries=record.get('deliveries') or []
    return deliveries[0]['department'] if deliveries else record.get('recommended') or 'Not routed'

def terms(texts,limit=12):
    counts=Counter()
    for text in texts:
        words=[w for w in re.findall(r"[a-z][a-z']+",text.lower()) if w not in STOPWORDS and len(w)>2]
        counts.update(set(words))
    return [{'term':t,'count':n} for t,n in counts.most_common(limit)]

def group(rows,key):
    groups={}
    for r in rows:groups.setdefault(key(r),[]).append(r)
    out=[]
    for name,items in groups.items():
        times=[m for m in (minutes(r) for r in items) if m is not None]
        out.append({'name':name,'count':len(items),'resolved':len(times),'median_minutes':percentile(times,.5),'p90_minutes':percentile(times,.9)})
    return sorted(out,key=lambda g:(-g['count'],g['name']))

def stats(all_records,days,site,now=None):
    now=now or time.time();since=now-days*86400
    rows=[r for r in all_records if r.get('transcript') and (created_at(r) or 0)>=since]
    times=[m for m in (minutes(r) for r in rows) if m is not None]
    ratings=[r['feedback']['rating'] for r in rows if r.get('feedback')]
    compared=[r for r in rows if r.get('deliveries') and r.get('recommended')]
    hours=[0]*24
    for r in rows:hours[time.localtime(created_at(r)).tm_hour]+=1
    daily={}
    for r in rows:daily.setdefault(time.strftime('%Y-%m-%d',time.localtime(created_at(r))),[]).append(r)
    slowest=sorted(((minutes(r),r) for r in rows if minutes(r) is not None),key=lambda x:-x[0])[:5]
    waiting=sorted(((max(0, (now-created_at(r))/60), r) for r in rows if r.get('resolved') is None),key=lambda x:-x[0])
    return {
        'site':site,'days':days,'generated':now,
        'attention':{'threshold_minutes':LONG_WAIT_MINUTES,
            'open':len(waiting),'long_waits':sum(m>=LONG_WAIT_MINUTES for m,r in waiting),
            'longest_minutes':round(waiting[0][0],1) if waiting else None,
            'requests':[{'request_id':r['request_id'],'room':r.get('room'),
                'department':department(r),'minutes':round(m,1)} for m,r in waiting[:5]]},
        'totals':{'requests':len(rows),'resolved':len(times),'open':len(rows)-len(times),
            'median_minutes':percentile(times,.5),'p90_minutes':percentile(times,.9),
            'avg_rating':round(sum(ratings)/len(ratings),2) if ratings else None,'ratings':len(ratings)},
        'categories':group(rows,lambda r:r.get('category') or 'Not classified'),
        'departments':group(rows,department),
        'languages':[{'name':k,'count':v} for k,v in Counter(r.get('language') or 'unknown' for r in rows).most_common()],
        'hours':hours,
        'daily':[{'date':d,'count':len(items),'median_minutes':percentile([m for m in (minutes(r) for r in items) if m is not None],.5)} for d,items in sorted(daily.items())],
        'agreement':{'compared':len(compared),'agreed':sum(department(r)==r['recommended'] for r in compared)},
        'common_terms':terms(r.get('transcript_en') or r['transcript'] for r in rows),
        'slowest':[{'request_id':r['request_id'],'room':r.get('room'),'category':r.get('category'),'department':department(r),'minutes':round(m,1)} for m,r in slowest],
    }
