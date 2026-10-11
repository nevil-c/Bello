from test_backend import Tests
class FeedbackTests(Tests):
 def test_feedback_validation_duplicates(self):
  import json
  path='/api/requests/8/feedback'
  self.assertEqual(self.post(path,json.dumps({'rating':0,'resolution_answer':'yes'}).encode())[0],400)
  body=json.dumps({'rating':5,'resolution_answer':'yes','comment':'Thank you'}).encode()
  self.assertEqual(self.post(path,body)[0],201)
  self.assertEqual(self.post(path,body)[0],409)
if __name__=='__main__':
 import unittest
 unittest.main()
