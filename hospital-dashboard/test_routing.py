import io,json,time,unittest
from unittest.mock import patch
import admin_stats
import aws_sync
import backend as b
import decision
import translation
import test_backend as _tb

ROUTED={'category':'Room Environment / Cleaning','department':'Housekeeping & Facilities','urgency':2,
    'urgency_confidence':.61,'more_info_needed':False,'scores':{'more_info_needed':.04}}

class RoutingTests(unittest.TestCase):
 setUp=_tb.Tests.setUp;post=_tb.Tests.post;get=_tb.Tests.get  # helpers only
 def spoken(self,text='Please bring a clean blanket.',language='en'):
  rid=self.post('/api/requests',json.dumps({'room':'412 · B','ui_language':'yue'}).encode())[1]['request_id']
  self.assertEqual(self.post(f'/api/requests/{rid}/audio',b'RIFF0000WAVEfixture')[0],202)
  with patch.object(b,'transcribe',return_value={'text':text,'language':language}):b.process_next()
  return rid
 def row(self,rid):
  with b.db() as c:return dict(c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone())

 def test_transcript_reaches_patient_before_routing(self):
  rid=self.spoken()
  status=self.get(f'/api/requests/{rid}')[1]
  self.assertEqual(status['transcript'],'Please bring a clean blanket.');self.assertEqual(status['state'],'Classifying')
  with patch.object(decision,'classify',return_value=ROUTED) as model,patch.object(translation,'to_english') as translate:
   self.assertTrue(b.process_enrich())
  translate.assert_not_called();model.assert_called_once_with('Please bring a clean blanket.')
  r=self.row(rid)
  self.assertEqual((r['state'],r['recommended'],r['urgency'],r['category'],r['transcript_en']),
   ('Ready to send','Housekeeping & Facilities',2,'Room Environment / Cleaning','Please bring a clean blanket.'))
  self.assertIsNone(r['review'])
  self.assertFalse(b.process_enrich())

 def test_other_languages_are_translated_for_the_model(self):
  rid=self.spoken('我想要多一張被','yue')
  with patch.object(translation,'to_english',return_value='I want another blanket') as translate,patch.object(decision,'classify',return_value=ROUTED) as model:
   b.process_enrich()
  translate.assert_called_once_with('我想要多一張被','yue');model.assert_called_once_with('I want another blanket')
  r=self.row(rid);self.assertEqual(r['transcript'],'我想要多一張被');self.assertEqual(r['transcript_en'],'I want another blanket')

 def test_whisper_engine_translates_the_recording(self):
  rid=self.spoken('我想要多一張被','yue')
  with patch.object(b,'EN_ENGINE','whisper'),patch.object(b,'translate_to_english',return_value='Another blanket please') as whisper,patch.object(decision,'classify',return_value=ROUTED):
   b.process_enrich()
  self.assertEqual(whisper.call_args.args[0],b'RIFF0000WAVEfixture');self.assertEqual(self.row(rid)['transcript_en'],'Another blanket please')

 def test_failures_leave_the_request_for_clerk_review(self):
  rid=self.spoken('Hola','es')
  with patch.object(translation,'to_english',side_effect=RuntimeError('model missing')):b.process_enrich()
  r=self.row(rid);self.assertEqual(r['state'],'Ready to send');self.assertIsNone(r['recommended']);self.assertIn('Translation',r['review'])
  rid=self.spoken()
  with patch.object(decision,'classify',side_effect=RuntimeError('no checkpoint')):b.process_enrich()
  r=self.row(rid);self.assertEqual(r['state'],'Ready to send');self.assertIn('Routing model',r['review'])
  self.assertEqual(self.post(f'/api/requests/{rid}/dispatch',json.dumps({'department':'Primary Nurse'}).encode())[0],201)

 def test_more_information_is_flagged(self):
  rid=self.spoken('It hurts')
  with patch.object(decision,'classify',return_value=dict(ROUTED,more_info_needed=True)):b.process_enrich()
  self.assertIn('more detail',self.row(rid)['review'])

 def test_typed_and_empty_requests(self):
  typed=lambda text:self.post('/api/requests',json.dumps({'room':'5','text':text,'ui_language':'yue'}).encode())[1]
  self.assertEqual(self.row(typed('blanket please')['request_id'])['language'],'en')
  self.assertEqual(self.row(typed('我要被')['request_id'])['language'],'yue')
  empty=self.post('/api/requests',json.dumps({'room':'5','confirmed_empty':True}).encode())[1]
  r=self.row(empty['request_id']);self.assertEqual(r['state'],'Ready to send');self.assertIn('Empty request',r['review'])
  self.assertEqual(self.post('/api/requests',json.dumps({'room':'5','ui_language':'klingon'}).encode())[0],400)

 def test_send_and_call(self):
  rid=self.spoken('我想要多一張被','yue')
  with patch.object(translation,'to_english',return_value='I want another blanket'),patch.object(decision,'classify',return_value=ROUTED):b.process_enrich()
  result=self.post(f'/api/requests/{rid}/dispatch',json.dumps({'department':'Housekeeping & Facilities','call':True}).encode())
  self.assertEqual(result[0],201);self.assertTrue(result[1]['called']);self.assertEqual(result[1]['phone'],'2200')
  self.assertIsNotNone(self.row(rid)['called'])
  with b.db() as c:
   self.assertEqual(c.execute('SELECT transcript FROM deliveries WHERE request_id=?',(rid,)).fetchone()[0],'I want another blanket')
   events=[e[0] for e in c.execute('SELECT event FROM history WHERE request_id=?',(rid,))]
  self.assertTrue(any(e.startswith('Call placed to Housekeeping & Facilities (ext 2200') for e in events))
  self.assertEqual(self.post(f'/api/requests/{rid}/dispatch',json.dumps({'department':'Primary Nurse','call':'yes'}).encode())[0],400)

 def test_staff_done_resolves_every_open_request_for_the_bed(self):
  first,second=self.spoken(),self.spoken()
  other=self.post('/api/requests',json.dumps({'room':'300 · A','text':'water'}).encode())[1]['request_id']
  unfinished=self.post('/api/requests',json.dumps({'room':'412 · B'}).encode())[1]['request_id']
  self.assertEqual(self.get('/api/rooms/open?room=412+%C2%B7+B')[1]['open'],2)
  result=self.post('/api/rooms/resolve',json.dumps({'room':'412 · B'}).encode())
  self.assertEqual(result[1]['resolved'],2)
  self.assertIsNotNone(self.row(first)['resolved']);self.assertIsNotNone(self.row(second)['resolved'])
  self.assertIsNone(self.row(other)['resolved']);self.assertIsNone(self.row(unfinished)['resolved'])
  self.assertEqual(self.post('/api/rooms/resolve',json.dumps({'room':'412 · B'}).encode())[1]['resolved'],0)
  self.assertEqual(self.get('/api/rooms/open?room=412+%C2%B7+B')[1]['open'],0)
  self.assertEqual(self.post('/api/rooms/resolve',b'{}')[0],400)

 def test_live_stream_sends_the_request_list(self):
  class Pipe(io.BytesIO):
   def write(self,data):super().write(data);raise BrokenPipeError()
  h=object.__new__(b.Handler);h.path='/api/stream';h.headers={'X-Bello-Token':b.TOKEN};h.wfile=Pipe()
  sent=[];h.send_response=lambda code:sent.append(code);h.send_header=lambda *a:None;h.end_headers=lambda:None
  h.do_GET()
  chunk=h.wfile.getvalue().decode()
  self.assertEqual(sent,[200]);self.assertTrue(chunk.startswith('data: '))
  self.assertEqual([r['id'] for r in json.loads(chunk[6:])['requests']],[8])
  self.assertEqual(self.get('/api/stream',token=False)[0],401)

 def test_changes_wake_the_stream(self):
  before=b.VERSION[0];self.post('/api/requests',json.dumps({'room':'1','text':'water'}).encode())
  self.assertGreater(b.VERSION[0],before)

 def test_screen_strings(self):
  self.assertEqual(self.get('/api/i18n/yue')[1]['source'],'built-in table')
  self.assertEqual(self.get('/api/i18n/yue')[1]['strings']['yes'],'係')
  fake=lambda strings,language:{k:'['+language+'] '+v for k,v in strings.items()}
  with patch.object(translation,'translate_strings',side_effect=fake) as model:
   first=self.get('/api/i18n/es')[1];second=self.get('/api/i18n/es')[1]
  model.assert_called_once();self.assertEqual(first['strings']['yes'],'[es] Yes');self.assertIn('cached',second['source'])
  with patch.object(translation,'translate_strings',side_effect=RuntimeError('missing')):
   self.assertEqual(self.get('/api/i18n/fr')[0],503)
  self.assertEqual(self.get('/api/i18n/xx')[0],404);self.assertEqual(self.get('/api/i18n/la')[0],404)

 def test_demo_rows_are_routed_but_never_archived(self):
  with b.db() as c:c.execute('DELETE FROM requests')
  b.initialize(seed_demo=True)
  with b.db() as c:
   rows=c.execute("SELECT id,state,sample_text FROM requests").fetchall()
   self.assertTrue(rows and all(r['state']=='Classifying' and r['sample_text']==b.DEMO_MARK for r in rows))
   b.event(c,rows[0]['id'],'Resolved at the bedside by staff');aws_sync.ensure_schema(c)
   self.assertEqual(aws_sync.pending(c),[])

 def test_archive_record_carries_routing(self):
  rid=self.spoken()
  with patch.object(decision,'classify',return_value=ROUTED):b.process_enrich()
  self.post('/api/rooms/resolve',json.dumps({'room':'412 · B'}).encode())
  with b.db() as c:rec=aws_sync.record(c,rid,1.0)
  self.assertEqual((rec['category'],rec['urgency'],rec['more_info_needed'],rec['ui_language']),('Room Environment / Cleaning',2,False,'yue'))
  self.assertIsNotNone(rec['resolved']);self.assertNotIn('audio',rec)

 def test_api_only(self):
  self.assertEqual(len(self.get('/api/languages')[1]['languages']),100)
  self.assertEqual(self.get('/api/languages',token=False)[0],401)
  for page in ('/','/departments','/admin','/bello.js'):self.assertEqual(self.get(page)[0],404)

 def test_admin_stats_require_the_cloud(self):
  with patch.object(aws_sync,'config',return_value=None):self.assertEqual(self.get('/api/admin/stats')[0],503)
  self.assertEqual(self.get('/api/admin/stats?days=5')[0],400)
  with patch.object(aws_sync,'config',return_value={'url':'u','key':'k','site':'unit-4'}),patch.object(admin_stats,'records',side_effect=admin_stats.Unavailable('down')):
   self.assertEqual(self.get('/api/admin/stats')[0],502)

class DecisionTests(unittest.TestCase):
 def test_labels_map_to_dashboard_queues(self):
  class Agent:
   def predict_batch(self,states,questions):
    self.states=states
    return [{'answers':{'category':{'choice':'Dietary / Refreshment','probabilities':{'Dietary / Refreshment':.9}},
     'department':{'choice':'food services','probabilities':{'food services':.8}},
     'urgency':{'probabilities':{'0':.1,'2':.7,'5':.2},'answer_confidence':.55,'score':2.1},
     'more_info_needed':{'noul':.31}}}]
  agent=Agent()
  with patch.object(decision,'_agent',agent):result=decision.classify('Can I have some water?')
  self.assertEqual(agent.states,['Patient request: Can I have some water?'])
  self.assertEqual((result['department'],result['urgency'],result['more_info_needed']),('Food Services',3,True))
  self.assertEqual(set(decision.DEPARTMENTS.values()),set(b.TEAMS))

class AdminStatsTests(unittest.TestCase):
 def test_issues_and_response_times(self):
  now=time.time()
  def rec(rid,category,dept,minutes,language='en',delivered=None,rating=None,age=3600):
   created=now-age
   return {'request_id':rid,'room':'412 · B','transcript':'x','transcript_en':'Need a blanket please','language':language,'category':category,
    'recommended':dept,'created':created,'resolved':None if minutes is None else created+minutes*60,
    'deliveries':[{'department':delivered}] if delivered else [],'feedback':{'rating':rating} if rating else None,'history':[]}
  records=[rec(1,'Toileting','Primary Nurse',4,delivered='Primary Nurse',rating=5),rec(2,'Toileting','Primary Nurse',10,'yue',delivered='Housekeeping & Facilities'),
   rec(3,'Dietary / Refreshment','Food Services',None),rec(4,'Toileting','Primary Nurse',2,age=40*86400),{'request_id':5,'transcript':None,'history':[]}]
  s=admin_stats.stats(records,7,'unit-4',now)
  self.assertEqual((s['totals']['requests'],s['totals']['resolved'],s['totals']['open']),(3,2,1))
  self.assertEqual(s['totals']['median_minutes'],7.0);self.assertEqual(s['totals']['avg_rating'],5)
  self.assertEqual(s['categories'][0],{'name':'Toileting','count':2,'resolved':2,'median_minutes':7.0,'p90_minutes':9.4})
  self.assertEqual(s['agreement'],{'compared':2,'agreed':1})
  self.assertEqual({l['name'] for l in s['languages']},{'en','yue'})
  self.assertEqual(s['common_terms'][0],{'term':'blanket','count':3})
  self.assertEqual(s['slowest'][0]['request_id'],2);self.assertEqual(sum(s['hours']),3)

if __name__=='__main__':unittest.main()
