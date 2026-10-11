import io
import unittest
from contextlib import redirect_stdout
from mic_check import verdict

def run(rows,threshold=450):
 out=io.StringIO()
 with redirect_stdout(out):code=verdict(rows,threshold)
 return code,out.getvalue()

class Tests(unittest.TestCase):
 def test_silent_device_blames_the_input_not_the_gate(self):
  code,text=run([(3,0,False,False,1)]*10)
  self.assertEqual(code,1);self.assertIn('almost no signal',text)
 def test_quiet_voice_names_the_level_gate_and_suggests_a_value(self):
  code,text=run([(180,5,False,False,90)]*10)
  self.assertEqual(code,1);self.assertIn('never reaches the 450 level gate',text)
  self.assertIn('BELLO_MIN_SPEECH_RMS=63',text)
 def test_working_microphone_passes_and_reports_typical_level(self):
  code,text=run([(1200,5,False,True,1100)]*10)
  self.assertEqual(code,0);self.assertIn('this microphone works',text)
 def test_hum_is_reported_as_the_reason(self):
  code,text=run([(700,5,True,False,20)]*10)
  self.assertEqual(code,1);self.assertIn('steady tone',text)

if __name__=='__main__':unittest.main()
