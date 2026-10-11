import unittest
from types import SimpleNamespace
from unittest.mock import patch
import local_stt as stt

class Tests(unittest.TestCase):
 def test_original_language_and_cpu_options(self):
  model=SimpleNamespace(transcribe=lambda *a,**kw: ([SimpleNamespace(text=' Hola '),SimpleNamespace(text='mundo')],SimpleNamespace(language='es')))
  with patch.object(stt,'load_model',return_value=model):
   self.assertEqual(stt.transcribe(b'fixture','wav'),{'text':'Hola mundo','language':'es'})
 def test_silence(self):
  model=SimpleNamespace(transcribe=lambda *a,**kw: (iter([]),SimpleNamespace(language='en')))
  with patch.object(stt,'load_model',return_value=model):
   with self.assertRaisesRegex(RuntimeError,'No speech'):stt.transcribe(b'fixture','wav')
 def test_missing_model(self):
  from pathlib import Path
  with patch.object(stt,'MODEL_DIR',Path('/nonexistent/bello-model')),patch.object(stt,'_model',None):
   with self.assertRaisesRegex(RuntimeError,'prepare_model'):stt.load_model()
if __name__=='__main__':unittest.main()
