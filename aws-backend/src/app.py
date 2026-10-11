"""Bello cloud archive: API Gateway -> Lambda -> DynamoDB.

One item per patient request, keyed by site and request id. The Pi pushes a full
snapshot of each request (transcript, routing, acknowledgments, feedback, history)
with PUT; a newer `version` replaces an older one, so retries and replays are safe.
Request contents are never written to logs.
"""
import base64
import json
import os
import re
import time
from datetime import datetime, timezone
from decimal import Decimal

SITE=re.compile(r'[A-Za-z0-9_-]{1,40}')
MAX_BODY=256*1024
_table=None

def table():
    global _table
    if _table is None:
        import boto3
        _table=boto3.resource('dynamodb').Table(os.environ['TABLE_NAME'])
    return _table

_s3=None

def s3():
    global _s3
    if _s3 is None:
        import boto3
        _s3=boto3.client('s3')
    return _s3

class Bad(Exception):
    pass

def stamp(seconds):
    """Athena/Tableau timestamp text (UTC) from epoch seconds."""
    return datetime.fromtimestamp(float(seconds),timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

def minutes(start,end):
    return round((float(end)-float(start))/60,2) if start is not None and end is not None and end>=start else None

def analytics_rows(site,item):
    """Flatten a stored snapshot into one wide row for `requests` and one row per event for `events`.
    Column names match the Glue tables in template.yaml."""
    history=sorted(item['history'],key=lambda h:h['created'])
    delivery=item['deliveries'][0] if item['deliveries'] else None
    created=history[0]['created'] if history else None
    delivered=delivery['created'] if delivery else None
    acked=next((h['created'] for h in history if h['event'].startswith('Department acknowledged')),None)
    feedback=item['feedback']
    row={'site_id':site,'request_id':item['request_id'],'room':item['room'],'recommended_department':item['recommended'],
        'delivered_department':delivery['department'] if delivery else None,'language':item['language'],'state':item['state'],'transcript':item['transcript'],
        'created_at':stamp(created) if created is not None else None,'delivered_at':stamp(delivered) if delivered is not None else None,'acknowledged_at':stamp(acked) if acked is not None else None,
        'minutes_to_deliver':minutes(created,delivered),'minutes_to_acknowledge':minutes(delivered,acked),
        'rating':feedback['rating'] if feedback else None,'resolution_answer':feedback['resolution_answer'] if feedback else None,'feedback_comment':feedback['comment'] if feedback else None,
        'version':float(item['version']),'synced_at':stamp(item['synced_at'])}
    events=[{'site_id':site,'request_id':item['request_id'],'event':h['event'],'event_at':stamp(h['created'])} for h in history]
    return {k:v for k,v in row.items() if v is not None},events

def publish(site,rid,item):
    """Write this request's rows to S3 for Athena. One object per request and table, overwritten on
    every new version, so a request is never duplicated. Raises on failure so the Pi retries."""
    bucket=os.environ.get('ANALYTICS_BUCKET')
    if not bucket:return
    row,events=analytics_rows(site,item)
    name=f'{site}/{rid:012d}.json'
    for prefix,rows in (('requests',[row]),('events',events)):
        s3().put_object(Bucket=bucket,Key=f'{prefix}/{name}',Body=('\n'.join(json.dumps(r) for r in rows)+'\n').encode(),ContentType='application/json')

def reply(status,body):
    return {'statusCode':status,'headers':{'Content-Type':'application/json','Cache-Control':'no-store'},'body':json.dumps(body,default=lambda d:int(d) if d==int(d) else float(d))}

def text(value,limit,name,required=True):
    if value is None and not required:return None
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()):
        raise Bad(f'{name} must be a string of at most {limit} characters')
    return value

def number(value,name):
    if isinstance(value,bool) or not isinstance(value,(int,float,Decimal)):raise Bad(f'{name} must be a number')
    return Decimal(str(value))

def clean(rid,payload):
    """Validate the Pi's snapshot and keep only the fields the archive stores."""
    if not isinstance(payload,dict):raise Bad('JSON object required')
    if payload.get('request_id')!=rid:raise Bad('request_id must match the URL')
    deliveries=payload.get('deliveries',[]);history=payload.get('history',[]);feedback=payload.get('feedback')
    if not isinstance(deliveries,list) or len(deliveries)>20:raise Bad('deliveries must be a list of at most 20')
    if not isinstance(history,list) or len(history)>500:raise Bad('history must be a list of at most 500')
    item={
        'request_id':rid,
        'version':number(payload.get('version'),'version'),
        'room':text(payload.get('room'),50,'room'),
        'state':text(payload.get('state'),60,'state'),
        'recommended':text(payload.get('recommended'),60,'recommended',False),
        'transcript':text(payload.get('transcript'),20000,'transcript',False),
        'language':text(payload.get('language'),20,'language',False),
        'deliveries':[],'history':[],'feedback':None,
    }
    for d in deliveries:
        if not isinstance(d,dict):raise Bad('each delivery must be an object')
        item['deliveries'].append({'id':text(d.get('id'),64,'delivery id'),'department':text(d.get('department'),60,'department'),'status':text(d.get('status'),60,'delivery status'),'created':number(d.get('created'),'delivery created'),'transcript':text(d.get('transcript'),20000,'delivery transcript',False)})
    for h in history:
        if not isinstance(h,dict):raise Bad('each history entry must be an object')
        item['history'].append({'event':text(h.get('event'),300,'event'),'created':number(h.get('created'),'event created')})
    if feedback is not None:
        if not isinstance(feedback,dict):raise Bad('feedback must be an object or null')
        rating=feedback.get('rating')
        if type(rating) is not int or rating not in range(1,6) or feedback.get('resolution_answer') not in ('yes','partly','no'):raise Bad('feedback needs rating 1-5 and resolution_answer yes/partly/no')
        item['feedback']={'rating':rating,'resolution_answer':feedback['resolution_answer'],'comment':text(feedback.get('comment',''),2000,'comment',False) or '','created':number(feedback.get('created'),'feedback created')}
    return item

def key(site,rid):
    return {'pk':f'SITE#{site}','sk':f'REQ#{rid:012d}'}

def public(item):
    return {k:v for k,v in item.items() if k not in ('pk','sk','expires_at')}

def put(site,rid,body):
    try:
        item=clean(rid,json.loads(body,parse_float=Decimal))
    except (ValueError,TypeError):
        raise Bad('JSON object required')
    item.update(key(site,rid),site_id=site,synced_at=Decimal(str(time.time())))
    days=int(os.environ.get('RETENTION_DAYS','0') or 0)
    if days>0:item['expires_at']=int(time.time())+days*86400
    try:
        table().put_item(Item=item,ConditionExpression='attribute_not_exists(pk) OR version <= :v',ExpressionAttributeValues={':v':item['version']})
    except Exception as error:
        # An equal or newer version is already archived; the Pi can treat this as done.
        if type(error).__name__=='ConditionalCheckFailedException':return reply(200,{'request_id':rid,'status':'unchanged'})
        raise
    publish(site,rid,item)
    return reply(200,{'request_id':rid,'status':'stored'})

def get(site,rid):
    found=table().get_item(Key=key(site,rid),ConsistentRead=True).get('Item')
    return reply(200,public(found)) if found else reply(404,{'error':'Unknown request'})

def listing(site,params):
    try:limit=int(params.get('limit') or 50)
    except ValueError:raise Bad('limit must be a number')
    if not 1<=limit<=100:raise Bad('limit must be 1-100')
    args={'KeyConditionExpression':'pk = :p','ExpressionAttributeValues':{':p':f'SITE#{site}'},'Limit':limit}
    if params.get('cursor'):
        try:sk=base64.urlsafe_b64decode(params['cursor']).decode()
        except (ValueError,UnicodeDecodeError):raise Bad('Invalid cursor')
        if not re.fullmatch(r'REQ#\d{12}',sk):raise Bad('Invalid cursor')
        args['ExclusiveStartKey']={'pk':f'SITE#{site}','sk':sk}
    result=table().query(**args)
    body={'requests':[public(i) for i in result.get('Items',[])]}
    if 'LastEvaluatedKey' in result:body['next_cursor']=base64.urlsafe_b64encode(result['LastEvaluatedKey']['sk'].encode()).decode()
    return reply(200,body)

def handler(event,context):
    try:
        method=event.get('httpMethod');resource=event.get('resource');path=event.get('pathParameters') or {}
        site=path.get('site')
        if site is None or not SITE.fullmatch(site):raise Bad('Invalid site id')
        rid=None
        if 'id' in path:
            if not path['id'].isdigit() or len(path['id'])>12:raise Bad('Invalid request id')
            rid=int(path['id'])
        if (method,resource)==('PUT','/sites/{site}/requests/{id}'):
            body=event.get('body') or ''
            if event.get('isBase64Encoded'):body=base64.b64decode(body).decode()
            if len(body)>MAX_BODY:return reply(413,{'error':'Body too large'})
            return put(site,rid,body)
        if (method,resource)==('GET','/sites/{site}/requests/{id}'):return get(site,rid)
        if (method,resource)==('GET','/sites/{site}/requests'):return listing(site,event.get('queryStringParameters') or {})
        return reply(404,{'error':'Not found'})
    except Bad as error:
        return reply(400,{'error':str(error)})
    except Exception as error:
        print(f'archive error: {type(error).__name__}',flush=True)
        return reply(500,{'error':'Internal error'})
