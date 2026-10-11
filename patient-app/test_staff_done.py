"""'Staff: hold when done' needs a deliberate hold; a tap must not resolve the request.

Opens a Tk window: run where a display is available (the Pi's desktop session or a dev machine).
"""
import time
import tkinter as tk
import unittest
from unittest.mock import patch
import nurse_call

class Tests(unittest.TestCase):
 def setUp(self):
  self.root=tk.Tk();self.root.attributes=lambda *a,**k:None;self.root.geometry('1000x740+3000+100')
  with patch.object(nurse_call.i18n,'saved_language',return_value='en'):
   self.app=nurse_call.App(self.root,demo=True)
  self.app.voice.listen=lambda *a,**k:None
  self.app.start_call();self.app.on_done(self.app.flow,'Blanket please',7);self.pump(.1)
 def tearDown(self):self.root.destroy()
 def pump(self,seconds):
  end=time.monotonic()+seconds
  while time.monotonic()<end:self.root.update();time.sleep(.01)
 def face(self):return self.app.listen_staff.winfo_children()[0]
 def test_tap_does_nothing(self):
  self.face().event_generate('<ButtonPress-1>');self.pump(.2);self.face().event_generate('<ButtonRelease-1>');self.pump(2.2)
  self.assertTrue(self.app.open_here);self.assertEqual(self.app.screen,'listen')
 def test_hold_resolves_and_returns_home(self):
  self.face().event_generate('<ButtonPress-1>');self.pump(2.4)
  self.assertFalse(self.app.open_here);self.assertEqual(self.app.screen,'home')
  self.assertEqual(self.app.staff_notice,nurse_call.tr('staff_saved'))
 def test_another_request_keeps_the_bed_open(self):
  self.app.cancel();self.pump(.1)
  self.assertTrue(self.app.open_here);self.assertEqual(self.app.screen,'home')

if __name__=='__main__':unittest.main()
