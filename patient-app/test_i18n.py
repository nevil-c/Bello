import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import i18n

class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  patcher=patch.object(i18n,'SETTINGS_DIR',Path(self.tmp.name));patcher.start();self.addCleanup(patcher.stop)
  self.addCleanup(i18n.apply,'en',None)
 def test_every_language_has_strings_and_cantonese_is_complete(self):
  self.assertEqual(len(i18n.LANGUAGES),100);self.assertIn('yue',i18n.NAMES)
  self.assertEqual(set(i18n.local_strings('yue')),set(i18n.ENGLISH))
 def test_missing_strings_fall_back_to_english(self):
  i18n.apply('es',{'yes':'Sí'})
  self.assertEqual(i18n.tr('yes'),'Sí');self.assertEqual(i18n.tr('no'),'No')
  i18n.apply('en',{'yes':'ignored'});self.assertEqual(i18n.tr('yes'),'Yes')
 def test_language_and_downloaded_strings_are_remembered(self):
  self.assertEqual(i18n.saved_language(),'en')
  i18n.save('es',{'yes':'Sí'})
  self.assertEqual(i18n.saved_language(),'es');self.assertEqual(i18n.local_strings('es'),{'yes':'Sí'})
  (Path(self.tmp.name)/'settings.json').write_text(json.dumps({'ui_language':'nonsense'}))
  self.assertEqual(i18n.saved_language(),'en')
if __name__=='__main__':unittest.main()
