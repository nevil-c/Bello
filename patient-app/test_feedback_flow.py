"""Submit feedback sends the rating to the server, then returns to the home screen.

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
  self.app.open_feedback();self.pump(.1)
  self.app.select_rating(4);self.app.select_resolution('yes');self.app.comment.set('  Kind nurse ')
 def tearDown(self):self.root.destroy()
 def pump(self,seconds):
  end=time.monotonic()+seconds
  while time.monotonic()<end:self.root.update();time.sleep(.01)
 def submit_button(self):
  # button() returns the bordered holder frame; the Button is its only child.
  for holder in self.app.inner.winfo_children()[-1].winfo_children():
   button=holder.winfo_children()[0]
   if button.cget('text')==nurse_call.tr('submit_feedback'):return button
  self.fail('no Submit feedback button on the survey')
 def test_submit_sends_to_the_server_then_goes_home(self):
  with patch.object(nurse_call,'submit_feedback') as send:
   self.submit_button().invoke();self.pump(.5)
  send.assert_called_once_with(7,4,'yes','Kind nurse')
  self.assertEqual(self.app.screen,'home')
  self.assertEqual(self.app.staff_notice,nurse_call.tr('feedback_saved'))
  self.assertIn(7,self.app.feedback_done)
  self.assertFalse(self.app.feedback_busy)
 def test_home_no_longer_offers_to_rate_the_same_request(self):
  with patch.object(nurse_call,'submit_feedback'):
   self.submit_button().invoke();self.pump(.5)
  texts=[w.cget('text') for w in self.walk(self.app.inner) if isinstance(w,(tk.Button,tk.Label))]
  self.assertNotIn(nurse_call.tr('rate_last_service'),texts)
  self.assertIn(nurse_call.tr('feedback_saved'),texts)
 def test_a_failed_save_stays_on_the_survey_with_the_answers(self):
  with patch.object(nurse_call,'submit_feedback',side_effect=RuntimeError('The relay cannot reach the Bello server.')):
   self.submit_button().invoke();self.pump(.5)
  self.assertEqual(self.app.screen,'feedback')
  self.assertEqual(self.app.feedback_notice,'The relay cannot reach the Bello server.')
  self.assertEqual((self.app.rating.get(),self.app.resolution.get()),(4,'yes'))
  self.assertNotIn(7,self.app.feedback_done)
  self.assertFalse(self.app.feedback_busy)
 def test_missing_answers_are_not_sent(self):
  self.app.rating.set(0)
  with patch.object(nurse_call,'submit_feedback') as send:
   self.submit_button().invoke();self.pump(.3)
  send.assert_not_called()
  self.assertEqual(self.app.screen,'feedback')
 def walk(self,widget):
  yield widget
  for child in widget.winfo_children():yield from self.walk(child)

if __name__=='__main__':unittest.main()
