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
  self.app.select_rating(4);self.app.select_resolution('yes')
 def tearDown(self):self.root.destroy()
 def pump(self,seconds):
  end=time.monotonic()+seconds
  while time.monotonic()<end:self.root.update();time.sleep(.01)
 def settle(self,seconds=5):
  # The save runs on a thread and comes back through the UI's 60 ms poll: wait for it, not a fixed time.
  end=time.monotonic()+seconds
  while time.monotonic()<end and (self.app.feedback_busy or self.app.voice.q.qsize()):self.pump(.02)
  self.pump(.4)
 def submit_button(self):
  # button() returns the bordered holder frame; the Button is its only child.
  for holder in self.app.inner.winfo_children()[-1].winfo_children():
   button=holder.winfo_children()[0]
   if button.cget('text')==nurse_call.tr('submit_feedback'):return button
  self.fail('no Submit feedback button on the survey')
 def test_submit_sends_to_the_server_then_goes_home(self):
  with patch.object(nurse_call,'submit_feedback') as send:
   self.submit_button().invoke();self.settle()
  send.assert_called_once_with(7,4,'yes')
  self.assertEqual(self.app.screen,'home')
  self.assertEqual(self.app.staff_notice,nurse_call.tr('feedback_saved'))
  self.assertIn(7,self.app.feedback_done)
  self.assertFalse(self.app.feedback_busy)
 def test_home_no_longer_offers_to_rate_the_same_request(self):
  with patch.object(nurse_call,'submit_feedback'):
   self.submit_button().invoke();self.settle()
  texts=[w.cget('text') for w in self.walk(self.app.inner) if isinstance(w,(tk.Button,tk.Label))]
  self.assertNotIn(nurse_call.tr('rate_last_service'),texts)
  self.assertIn(nurse_call.tr('feedback_saved'),texts)
 def test_a_failed_save_stays_on_the_survey_with_the_answers(self):
  with patch.object(nurse_call,'submit_feedback',side_effect=RuntimeError('The relay cannot reach the Bello server.')):
   self.submit_button().invoke();self.settle()
  self.assertEqual(self.app.screen,'feedback')
  self.assertEqual(self.app.feedback_notice,'The relay cannot reach the Bello server.')
  self.assertEqual((self.app.rating.get(),self.app.resolution.get()),(4,'yes'))
  self.assertNotIn(7,self.app.feedback_done)
  self.assertFalse(self.app.feedback_busy)
 def test_a_failed_save_is_shown_in_red_and_logged(self):
  import io,contextlib
  err=io.StringIO()
  with patch.object(nurse_call,'submit_feedback',side_effect=RuntimeError('Unknown request')),contextlib.redirect_stderr(err):
   self.submit_button().invoke();self.settle()
  self.assertIn('Unknown request',err.getvalue())
  note=next(w for w in self.walk(self.app.inner) if isinstance(w,tk.Label) and w.cget('text')=='Unknown request')
  self.assertEqual(note.cget('fg'),self.app.t['stop'])
 def test_an_already_saved_answer_counts_as_saved(self):
  # The server keeps one answer per request; the retry of a double press or a lost Bluetooth reply is refused.
  with patch.object(nurse_call.voice_client,'api',side_effect=RuntimeError('Feedback already submitted for this request')):
   self.submit_button().invoke();self.settle()
  self.assertEqual(self.app.screen,'home')
  self.assertIn(7,self.app.feedback_done)
 def test_missing_answers_are_not_sent(self):
  self.app.rating.set(0)
  with patch.object(nurse_call,'submit_feedback') as send:
   self.submit_button().invoke();self.settle()
  send.assert_not_called()
  self.assertEqual(self.app.screen,'feedback')
  note=next(w for w in self.walk(self.app.inner) if isinstance(w,tk.Label) and w.cget('text')==nurse_call.tr('feedback_invalid'))
  self.assertEqual(note.cget('fg'),self.app.t['stop'])
 def walk(self,widget):
  yield widget
  for child in widget.winfo_children():yield from self.walk(child)

if __name__=='__main__':unittest.main()
