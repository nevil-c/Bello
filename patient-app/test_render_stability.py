"""Repaint stability: on screen the UI must not rebuild itself or rescale the layout.

Run on the device that shows the UI (the Pi's desktop session): python3 test_render_stability.py
"""
import time
import unittest
import tkinter as tk
import nurse_call

class Tests(unittest.TestCase):
 def setUp(self):
  self.root=tk.Tk()
  # Keep the test window out of the way; App() would otherwise take the whole screen.
  self.root.attributes=lambda *a,**k:None
  self.root.geometry('1000x740+3000+100')
  self.app=nurse_call.App(self.root,demo=True)
  self.app.voice.listen=lambda *a,**k:None
  self.pump(30)
 def tearDown(self):self.root.destroy()
 def pump(self,n,dt=.004):
  for _ in range(n):self.root.update();time.sleep(dt)
 def parts(self):
  body=self.app.root.winfo_children()[1];canvas=body.winfo_children()[0]
  return canvas,canvas.winfo_children()[0]
 def listen(self,partials=()):
  self.app.start_call()
  for text in partials:
   self.app.on_words(self.app.flow,text);self.pump(6)
  self.pump(10)

 def test_partial_transcript_does_not_rescale_or_rebuild_the_screen(self):
  self.listen(('My back is','My back is hurting and I need help sitting up, and I am dizzy'))
  renders,scale=self.app.gen,self.app.layout_scale
  for extra in (' and short of breath',' and my chest feels tight',' and I cannot lie flat'):
   self.app.on_words(self.app.flow,self.app.words+extra);self.pump(6)
  self.assertEqual(self.app.gen,renders)          # no rebuild while the patient talks
  self.assertEqual(self.app.layout_scale,scale)   # no rescale either

 def test_fit_shrinks_for_smaller_window_and_settles(self):
  self.listen()
  # Deterministic geometry: an off-screen window's real size is not reliable under a WM, and the
  # fit's own rebuild replaces the canvas, so re-fake whatever canvas is current.
  root=self.root
  real=(root.winfo_width,root.winfo_height)
  def fake():
   canvas,inner=self.parts()
   canvas.winfo_width=lambda:630;canvas.winfo_height=lambda:400
  root.winfo_width=lambda:700;root.winfo_height=lambda:520
  try:
   fake();before=self.app.gen;self.app.fit();self.pump(4)
   self.assertLess(self.app.layout_scale,1.0)
   self.assertGreaterEqual(self.app.gen,before+1,'the new scale has to be rebuilt once')
   fake();scale=self.app.layout_scale
   self.app.fit();self.app.fit();self.pump(10)
   self.assertEqual(self.app.layout_scale,scale,'the same window size must not re-fit')
  finally:
   (root.winfo_width,root.winfo_height)=real

 def test_pause_sweep_repaints_strips_not_whole_label_faces(self):
  self.listen()
  from background_sweep import BackgroundSweep
  sweep=BackgroundSweep(self.app.root,self.app.t['bg'],'#D8F3DC')
  labels=[s for s in sweep.surfaces if s[1]=='label']
  self.assertTrue(labels, 'the sweep should be washing the label surfaces')
  area=sum(w.winfo_width()*w.winfo_height() for w,kind,_,_ in labels)
  # Deterministic geometry: a window that is not realised reports a 0-width root, which makes
  # the boundary maths degenerate and the strip assertion vacuous.
  self.app.root.winfo_width=lambda:1000
  for widget,kind,_,_ in sweep.surfaces:widget.winfo_rootx=lambda:0
  stats={'alloc':0,'pixels':0}
  original=tk.PhotoImage
  class Counting(original):
   def __init__(inner_self,*a,**k):stats['alloc']+=1;original.__init__(inner_self,*a,**k)
   def put(inner_self,data,to=None):
    if to is not None:stats['pixels']+=(to[2]-to[0])*(to[3]-to[1])
    return original.put(inner_self,data,to=to)
  tk.PhotoImage=Counting
  frames=20
  try:
   started=time.perf_counter()
   for i in range(frames):
    # Sweep the boundary across the full width: a strip costs a fraction of a label face,
    # a whole-face repaint costs the face every frame and this bound catches it.
    sweep.update(i/(frames-1));self.root.update_idletasks()
   elapsed=(time.perf_counter()-started)*1000
  finally:
   tk.PhotoImage=original
  print(f'\nsweep: {elapsed/frames:.1f} ms/frame, bitmap allocs={stats["alloc"]} '
        f'({len(labels)} labels), pixels put={stats["pixels"]} over {frames} frames '
        f'({stats["pixels"]/frames:.0f}/frame), label area={area}')
  self.assertGreater(stats['pixels'],0,'the cells must actually be repainted')
  self.assertLessEqual(stats['alloc'],len(labels),'bitmaps are cut once, not rebuilt every frame')
  self.assertLess(stats['pixels'],2*area,'per-frame repaint must be strips, not whole faces')

if __name__=='__main__':unittest.main(verbosity=2)
