import base64,json,os,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent/'src'))
import app

class ConditionalCheckFailedException(Exception):
    pass

class FakeTable:
    """Just enough of a DynamoDB table: put with the version condition, get and query."""
    def __init__(self):self.items={}
    def put_item(self,Item,ConditionExpression,ExpressionAttributeValues):
        old=self.items.get((Item['pk'],Item['sk']))
        if old is not None and not old['version']<=ExpressionAttributeValues[':v']:raise ConditionalCheckFailedException()
        self.items[(Item['pk'],Item['sk'])]=Item
    def get_item(self,Key,ConsistentRead):
        item=self.items.get((Key['pk'],Key['sk']));return {'Item':item} if item else {}
    def query(self,KeyConditionExpression,ExpressionAttributeValues,Limit,ExclusiveStartKey=None):
        rows=sorted((v for (pk,sk),v in self.items.items() if pk==ExpressionAttributeValues[':p']),key=lambda i:i['sk'])
        if ExclusiveStartKey:rows=[r for r in rows if r['sk']>ExclusiveStartKey['sk']]
        page=rows[:Limit];out={'Items':page}
        if len(rows)>Limit:out['LastEvaluatedKey']={'pk':page[-1]['pk'],'sk':page[-1]['sk']}
        return out

class FakeS3:
    def __init__(self):self.objects={}
    def put_object(self,Bucket,Key,Body,ContentType):self.objects[(Bucket,Key)]=Body.decode()

def snapshot(rid=1,version=100.5,**extra):
    body={'request_id':rid,'version':version,'room':'412 B','recommended':'Primary Nurse','transcript':'Please bring a blanket.','language':'eng','state':'Delivered to queue',
        'deliveries':[{'id':'a'*32,'department':'Housekeeping & Facilities','status':'Acknowledged','created':99.0,'transcript':'Please bring a blanket.'}],
        'feedback':{'rating':5,'resolution_answer':'yes','comment':'Thanks','created':101.0},
        'history':[{'event':'Patient request created','created':90.0}]}
    body.update(extra);return body

def call(method,resource,site='unit-4',rid=None,body=None,query=None):
    event={'httpMethod':method,'resource':resource,'pathParameters':{'site':site},'queryStringParameters':query,'body':json.dumps(body) if body is not None else None}
    if rid is not None:event['pathParameters']['id']=str(rid)
    out=app.handler(event,None);return out['statusCode'],json.loads(out['body'])

PUT='/sites/{site}/requests/{id}'
LIST='/sites/{site}/requests'

class Tests(unittest.TestCase):
    def setUp(self):
        app._table=FakeTable();app._s3=FakeS3();os.environ.pop('RETENTION_DAYS',None);os.environ.pop('ANALYTICS_BUCKET',None)
    def test_store_and_read_back(self):
        self.assertEqual(call('PUT',PUT,rid=1,body=snapshot())[1]['status'],'stored')
        status,body=call('GET',PUT,rid=1)
        self.assertEqual(status,200);self.assertEqual(body['transcript'],'Please bring a blanket.');self.assertEqual(body['feedback']['rating'],5)
        self.assertNotIn('pk',body);self.assertNotIn('expires_at',body)
    def test_older_snapshot_does_not_overwrite_newer(self):
        call('PUT',PUT,rid=1,body=snapshot(version=200,state='Delivered to queue'))
        self.assertEqual(call('PUT',PUT,rid=1,body=snapshot(version=150,state='Ready to send'))[1]['status'],'unchanged')
        self.assertEqual(call('GET',PUT,rid=1)[1]['state'],'Delivered to queue')
    def test_same_version_retry_is_accepted(self):
        call('PUT',PUT,rid=1,body=snapshot());self.assertEqual(call('PUT',PUT,rid=1,body=snapshot())[1]['status'],'stored')
    def test_sites_are_separate(self):
        call('PUT',PUT,site='unit-4',rid=1,body=snapshot());self.assertEqual(call('GET',PUT,site='unit-5',rid=1)[0],404)
        self.assertEqual(call('GET',LIST,site='unit-5')[1]['requests'],[])
    def test_validation(self):
        bad=[snapshot(rid=2),snapshot(room=''),snapshot(room='x'*51),snapshot(version='now'),snapshot(feedback={'rating':9,'resolution_answer':'yes'}),snapshot(history=[{'event':'x','created':'y'}]),snapshot(deliveries='no'),snapshot(transcript=5)]
        for body in bad:
            with self.subTest(body=str(body)[:60]):self.assertEqual(call('PUT',PUT,rid=1,body=body)[0],400)
        self.assertEqual(call('PUT',PUT,site='bad site!',rid=1,body=snapshot())[0],400)
        self.assertEqual(call('GET',PUT,rid='abc')[0],400)
        self.assertEqual(call('PUT',PUT,rid=1)[0],400)
    def test_oversized_body(self):
        self.assertEqual(call('PUT',PUT,rid=1,body=snapshot(transcript='x'*(300*1024)))[0],413)
    def test_listing_pages_in_order(self):
        for i in (3,1,2):call('PUT',PUT,rid=i,body=snapshot(rid=i))
        status,first=call('GET',LIST,query={'limit':'2'})
        self.assertEqual([r['request_id'] for r in first['requests']],[1,2]);self.assertIn('next_cursor',first)
        second=call('GET',LIST,query={'limit':'2','cursor':first['next_cursor']})[1]
        self.assertEqual([r['request_id'] for r in second['requests']],[3]);self.assertNotIn('next_cursor',second)
    def test_bad_cursor_and_limit(self):
        self.assertEqual(call('GET',LIST,query={'cursor':base64.urlsafe_b64encode(b'SITE#other').decode()})[0],400)
        self.assertEqual(call('GET',LIST,query={'limit':'0'})[0],400);self.assertEqual(call('GET',LIST,query={'limit':'x'})[0],400)
    def test_retention_sets_expiry(self):
        os.environ['RETENTION_DAYS']='30';call('PUT',PUT,rid=1,body=snapshot())
        self.assertGreater(list(app._table.items.values())[0]['expires_at'],0)
    def test_unknown_route_and_server_error(self):
        self.assertEqual(call('DELETE',PUT,rid=1)[0],404)
        class Broken:
            def put_item(self,**k):raise RuntimeError('secret transcript text')
        app._table=Broken();status,body=call('PUT',PUT,rid=1,body=snapshot())
        self.assertEqual(status,500);self.assertNotIn('secret',json.dumps(body))

class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        app._table=FakeTable();app._s3=FakeS3();os.environ['ANALYTICS_BUCKET']='bkt';self.addCleanup(os.environ.pop,'ANALYTICS_BUCKET',None)
    def rows(self,key):return [json.loads(l) for l in app._s3.objects[('bkt',key)].splitlines()]
    def test_flat_rows_for_sql(self):
        history=[{'event':'Patient request created; pending clerk review','created':1760000000.0},{'event':'Saved transcript delivered to Housekeeping & Facilities','created':1760000060.0},{'event':'Department acknowledged: Housekeeping & Facilities','created':1760000240.0}]
        call('PUT',PUT,rid=7,body=snapshot(rid=7,version=1760000300.0,history=history,deliveries=[{'id':'a'*32,'department':'Housekeeping & Facilities','status':'Acknowledged','created':1760000060.0,'transcript':'Blanket'}],feedback={'rating':4,'resolution_answer':'partly','comment':'Slow','created':1760000290.0}))
        row=self.rows('requests/unit-4/000000000007.json')[0]
        self.assertEqual(row['site_id'],'unit-4');self.assertEqual(row['request_id'],7);self.assertEqual(row['created_at'],'2025-10-09 08:53:20')
        self.assertEqual(row['minutes_to_deliver'],1.0);self.assertEqual(row['minutes_to_acknowledge'],3.0);self.assertEqual(row['rating'],4)
        self.assertEqual(row['delivered_department'],'Housekeeping & Facilities')
        events=self.rows('events/unit-4/000000000007.json')
        self.assertEqual([e['event_at'] for e in events],['2025-10-09 08:53:20','2025-10-09 08:54:20','2025-10-09 08:57:20']);self.assertEqual({e['request_id'] for e in events},{7})
    def test_request_without_delivery_or_feedback_omits_nulls(self):
        call('PUT',PUT,rid=2,body=snapshot(rid=2,deliveries=[],feedback=None,transcript=None,state='Awaiting recording'))
        row=self.rows('requests/unit-4/000000000002.json')[0]
        for column in ('delivered_at','acknowledged_at','rating','transcript','minutes_to_acknowledge'):self.assertNotIn(column,row)
    def test_new_version_overwrites_same_object(self):
        call('PUT',PUT,rid=3,body=snapshot(rid=3,version=10,state='Ready to send'));call('PUT',PUT,rid=3,body=snapshot(rid=3,version=20,state='Delivered to queue'))
        self.assertEqual(len([k for k in app._s3.objects if k[1].startswith('requests/')]),1);self.assertEqual(self.rows('requests/unit-4/000000000003.json')[0]['state'],'Delivered to queue')
    def test_stale_snapshot_is_not_published(self):
        call('PUT',PUT,rid=4,body=snapshot(rid=4,version=20,state='Delivered to queue'));call('PUT',PUT,rid=4,body=snapshot(rid=4,version=10,state='Ready to send'))
        self.assertEqual(self.rows('requests/unit-4/000000000004.json')[0]['state'],'Delivered to queue')
    def test_s3_failure_returns_error_so_pi_retries(self):
        class Down:
            def put_object(self,**k):raise RuntimeError('s3 down')
        app._s3=Down();self.assertEqual(call('PUT',PUT,rid=5,body=snapshot(rid=5))[0],500)
        app._s3=FakeS3();self.assertEqual(call('PUT',PUT,rid=5,body=snapshot(rid=5))[1]['status'],'stored');self.assertTrue(app._s3.objects)

if __name__=='__main__':unittest.main()
