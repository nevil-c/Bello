"""
Bedside Nurse Call - patient screen (prototype)

Run:   python nurse_call.py
Demo:  python nurse_call.py --demo    (pretends the patient spoke; no microphone needed)

Keys:  F11 full screen  |  Esc closes the menu / leaves full screen
       Space or Enter on the start screen presses CALL

Flow:  CALL  ->  recording ding  ->  patient speaks,
       words appear on screen  ->  sent to the nurse.  One Cancel button.

Microphone audio is uploaded to the Bello backend for local speech recognition transcription.
Install requirements.txt and configure BELLO_SERVER and BELLO_API_TOKEN.
"""
import os
from voice_client import capture_and_submit, record, submit_audio, submit_text, submit_empty, submit_feedback, Cancelled
from voice_client import resolve_room, open_requests, ui_strings
import voice_client
import i18n
from i18n import tr
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

ROOM, BED = os.environ.get("BELLO_ROOM", "412"), os.environ.get("BELLO_BED", "B")
DEMO_SPEECH = "My back is hurting and I need help sitting up."

# Auto-fit calibration. A canvas below MIN_CANVAS px is a window still being mapped, so its size
# is not worth scaling to; MIN_LAYOUT_SCALE stops a pathological window from shrinking the UI to
# the point of being unreadable (it clips instead).
MIN_CANVAS = 200
MIN_LAYOUT_SCALE = 0.20
FIT_SLACK = 0.97       # margin for borders that stay a fixed number of pixels at any scale
SLOW_REBUILD_MS = 50   # report a screen build slower than this; the Pi vs the Mac numbers

THEMES = {
    "standard": dict(bg="#FFFFFF", ink="#14232B", muted="#5A6B73", line="#D3DDE1",
                     call="#D2402B", halo="#F7D5CF", on="#FFFFFF",
                     listen="#1D5C8C", soft="#E3EEF6", ok="#1E7A4C",
                     stop="#C62828", stop_on="#FFFFFF", stop_dark="#9E1F1F"),
    "contrast": dict(bg="#000000", ink="#FFFFFF", muted="#FFFFFF", line="#FFFFFF",
                     call="#FFD400", halo="#4D4000", on="#000000",
                     listen="#FFD400", soft="#1A1A1A", ok="#FFD400",
                     stop="#FF3B30", stop_on="#000000", stop_dark="#CC2F26"),
    "night": dict(bg="#1C1612", ink="#E8D9C4", muted="#A8957C", line="#3A2F26",
                  call="#C9662E", halo="#3B2618", on="#1C1612",
                  listen="#D29A5A", soft="#2A211A", ok="#B8A35A",
                  stop="#B23A2A", stop_on="#F3E6D3", stop_dark="#8E2E21"),
}
FILTERS = [("standard", "filter_standard"), ("contrast", "filter_contrast"), ("night", "filter_night")]
HOLD_MS = 2000   # staff hold "Staff: done" this long, so a patient's tap does not resolve the request

# ---------------------------------------------------------------- speech

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

SAY_PS = ("Add-Type -AssemblyName System.Speech; "
          "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          "$s.Rate = -1; $s.Speak('{text}')")

# Prints "H:<words so far>" while the patient speaks, then "R:<final words>".
LISTEN_PS = r"""
[Console]::OutputEncoding = [Text.Encoding]::UTF8
Add-Type -AssemblyName System.Speech
$r = New-Object System.Speech.Recognition.SpeechRecognitionEngine
$r.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
$r.SetInputToDefaultAudioDevice()
$r.InitialSilenceTimeout = [TimeSpan]::FromSeconds(10)
$r.EndSilenceTimeout = [TimeSpan]::FromSeconds(1.5)
Register-ObjectEvent $r SpeechHypothesized -SourceIdentifier hyp | Out-Null
Register-ObjectEvent $r RecognizeCompleted -SourceIdentifier done | Out-Null
$r.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Single)
while ($true) {
  $e = Wait-Event -Timeout 40
  if (-not $e) { break }
  Remove-Event -EventIdentifier $e.EventIdentifier
  if ($e.SourceIdentifier -eq 'hyp') {
    [Console]::Out.WriteLine('H:' + $e.SourceEventArgs.Result.Text); [Console]::Out.Flush()
  } else {
    $res = $e.SourceEventArgs.Result
    if ($res) { [Console]::Out.WriteLine('R:' + $res.Text) }
    break
  }
}
"""


class Voice:
    """Speaks and listens on background threads. Results come back through `self.q`."""

    def __init__(self, demo=False):
        self.demo = demo
        if not demo and os.environ.get("BELLO_LIVE_ENGINE","whisper")=="vosk":
            from streaming_stt import load_model
            load_model()
        self.windows = sys.platform == "win32"
        self.q = queue.Queue()
        self._proc = None
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    def _start(self, script):
        try:
            p = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 creationflags=NO_WINDOW)
        except OSError:
            return None
        with self._lock:
            self._proc = p
        return p

    def stop(self):
        self._cancel.set()
        with self._lock:
            p, self._proc = self._proc, None
        if p and p.poll() is None:
            p.kill()

    def say(self, text, then=None):
        """Keep existing screen transitions without producing any audio."""
        self.stop()
        if then:self.q.put((then, ()))

    def listen(self, on_words, on_done, on_error, on_processing, on_pause, on_level):
        self.stop()
        cancel = threading.Event()
        self._cancel = cancel
        def work():
            try:
                request_id = None
                if self.demo:
                    final = DEMO_SPEECH
                else:
                    result = capture_and_submit(f"{ROOM} · {BED}", cancel,
                        lambda text: self.q.put((on_words, (text,))),
                        lambda: self.q.put((on_processing, ())),
                        lambda progress: self.q.put((on_pause, (progress,))),
                        lambda level: self.q.put((on_level, (level,))))
                    if result is None:
                        self.q.put((on_done, (None,)))
                        return
                    final = result['transcript']
                    request_id = result['request_id']
                if not cancel.is_set():
                    self.q.put((on_words, (final,)))
                    self.q.put((on_done, (final, request_id)))
            except Cancelled:
                pass
            except Exception as error:
                if not cancel.is_set():
                    self.q.put((on_error, (str(error),)))
        threading.Thread(target=work, daemon=True).start()


def blend(a, b, t):
    """Mix hex colour a toward b by t (0..1)."""
    a = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(a, b))


# ---------------------------------------------------------------- app

class App:
    def __init__(self, root, demo=False):
        self.root = root
        self.voice = Voice(demo)
        self.dpi = root.winfo_fpixels("1i") / 96.0
        self.layout_scale = 1.0  # the content's scale; px() uses it
        self.chrome_scale = 1.0  # the header's scale: always the call flow's, so it never zooms
        self.built_key = None    # (screen, chrome) currently built
        self.chrome_key = None   # theme/large/a11y the current chrome was built for
        self.built_gen = 0       # gen the current content belongs to
        self.fitted = {}         # fit_key() -> the layout_scale that fits it
        self.painted = None      # theme colours the kept widgets currently wear

        self.screen = "home"     # home | listen | type
        self.state = "speaking"  # speaking | listening | sent | nothing   (listen screen)
        self.words = ""
        self.tries = 0
        self.keyboard = False
        self.filter_i = 0
        self.large = False
        self.a11y_open = False
        self.lang_open = False
        self.open_here = False   # this bed has a request staff have not marked done
        self.staff_notice = ""
        self.hold_job = None
        self.resolving = False
        self.flow = 0            # bumps whenever a voice exchange is abandoned
        self.gen = 0             # bumps on every redraw (stops old animations)
        self.typed = tk.StringVar()
        self.words_label = None
        self.pause_started = None
        self.pause_display = 0.0
        self.cancel_progress = None
        self.background_sweep = None
        self.mic_visual = None
        self.request_id = None
        self.feedback_done = set()
        self.feedback_busy = False
        self.feedback_notice = ""
        self.rating = tk.IntVar(value=0)
        self.resolution = tk.StringVar(value="")
        self.comment = tk.StringVar()

        families = set(tkfont.families())
        self.family = next((f for f in ("Atkinson Hyperlegible", "Segoe UI", "Helvetica")
                            if f in families), "TkDefaultFont")

        root.protocol("WM_DELETE_WINDOW", lambda: (self.voice.stop(), root.destroy()))
        root.title("Nurse Call")
        root.geometry(f"{self.px(1000)}x{self.px(740)}")
        root.minsize(min(self.px(820), root.winfo_screenwidth()),
                     min(self.px(640), root.winfo_screenheight()))
        # Start as a borderless kiosk on the current display.
        root.attributes("-fullscreen", True)
        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", self.on_escape)
        root.bind("<space>", self.on_call_key)
        root.bind("<Return>", self.on_call_key)

        # Says what the device this runs on actually reports, and which checkout is running.
        print(f"UI: {os.path.abspath(__file__)} on Tk {root.tk.call('info', 'patchlevel')}, "
              f"session {'wayland' if os.environ.get('WAYLAND_DISPLAY') else os.environ.get('DISPLAY', 'none')}, "
              f"screen {root.winfo_screenwidth()}x{root.winfo_screenheight()}, "
              f"dpi {self.dpi:.2f}, scale {self.layout_scale:.3f}", flush=True)

        code = i18n.saved_language()
        i18n.apply(code, i18n.local_strings(code))
        voice_client.UI_LANGUAGE = code
        if not demo:
            if code != "en":
                self.fetch_strings(code)
            self.background(lambda: open_requests(f"{ROOM} · {BED}"), self.set_open)

        self.render()
        self.poll()

    # ---- helpers
    @property
    def t(self):
        theme=THEMES[FILTERS[self.filter_i][0]]
        if self.screen=='listen' and self.state in ('processing','sent'):
            return dict(theme,bg='#B7E4C7' if self.state=='sent' else '#D8F3DC',
                        soft='#D8F3DC' if self.state=='sent' else theme['soft'])
        return theme

    def px(self, n):
        return max(1, int(n * self.dpi * self.layout_scale))

    def font(self, size, bold=False):
        scale = 1.85 if self.large else 1.6
        return (self.family, -self.px(size * scale), "bold" if bold else "normal")

    def poll(self):
        try:
            while True:
                fn, args = self.voice.q.get_nowait()
                if fn:
                    fn(*args)
        except queue.Empty:
            pass
        self.root.after(60, self.poll)

    def clickable(self, widget, cmd):
        widget.configure(cursor="hand2")
        widget.bind("<Button-1>", lambda e: cmd())
        for child in widget.winfo_children():
            self.clickable(child, cmd)

    def button(self, parent, text, cmd, size=22, danger=False):
        t = self.t
        bg, fg, active = (t["stop"], t["stop_on"], t["stop_dark"]) if danger else (t["bg"], t["ink"], t["soft"])
        holder = tk.Frame(parent, bg=t["stop"] if danger else t["ink"], padx=2, pady=2)
        tk.Button(holder, text=text, command=cmd, font=self.font(size, True),
                  bg=bg, fg=fg, activebackground=active, activeforeground=fg,
                  relief="flat", bd=0, padx=self.px(40), pady=self.px(14), cursor="hand2",
                  highlightthickness=0).pack()
        return holder

    def label(self, parent, text, size=20, bold=False, color="ink", wrap=0, bg="bg"):
        return tk.Label(parent, text=text, font=self.font(size, bold), fg=self.t[color],
                        bg=self.t[bg], wraplength=self.px(wrap) if wrap else 0, justify="center")

    # ---- drawing
    def render(self, force=False):
        # A rebuild destroys and re-creates every widget, and each Tk widget is its own X window:
        # on a Pi without a compositor you watch them get unmapped and remapped one by one, which
        # is the flash on every press. So: reuse the chrome, rebuild only the content, and for a
        # state change on the same screen reconfigure the widgets we already have.
        chrome_key = (self.large, self.a11y_open, self.lang_open, self.filter_i, i18n.language)
        # The home screen grows a staff button while a request is open; it needs a rebuild.
        content_key = (self.screen, chrome_key, self.screen == "home" and (self.open_here, bool(self.staff_notice)))
        rescale = False
        if self.chrome_key is not None:
            # Build at the fitted scale straight away rather than build, fit and build again.
            # Only the call flow's scale touches the header: into and out of the survey rescales
            # the content alone, so the whole window is not torn down (it repainted as blank).
            key = self.fit_key()
            flow = self.scale_for(key[:3] + (False,))
            content = self.scale_for(key) if key[3] else flow
            if flow is not None and abs(flow - self.chrome_scale) > .005:
                self.chrome_scale, force = flow, True
            if content is not None and abs(content - self.layout_scale) > .005:
                self.layout_scale, rescale = content, True
        rechrome = force or self.chrome_key != chrome_key
        recontent = rechrome or rescale or self.built_key != content_key
        self.gen += 1
        self.pause_started = None
        self.pause_display = 0.0
        self.cancel_progress = None
        if self.background_sweep is not None:
            # The sweep paints into widgets that a refresh keeps, so it has to be taken off them.
            self.background_sweep.clear()
        self.background_sweep = None
        self.mic_visual = None
        t = self.t
        if not recontent:
            # Same screen, new state. The content is current for this generation, so the layout
            # and fit handlers must keep working (they are keyed on it).
            self.built_gen = self.gen
            self.repaint(t)
            self.refresh()
            return
        self.built_key = content_key
        self.chrome_key = chrome_key
        started = time.perf_counter()
        if rechrome:
            self.painted = t
            # No layout_scale reset: the fit owns it, and resetting here would discard the scale
            # the fit just measured (it rebuilds through this path) and pop the page every time.
            for w in self.root.winfo_children():
                w.destroy()
            content_scale, self.layout_scale = self.layout_scale, self.chrome_scale
            self.root.configure(bg=t["bg"])
            top = tk.Frame(self.root, bg=t["bg"])
            top.pack(fill="x", padx=self.px(24), pady=(self.px(16), 0))
            self.label(top, f"{tr('room')} {ROOM}   ·   {tr('bed')} {BED}", 17, color="muted").pack(side="left")
            self.a11y_button(top).pack(side="right")
            body = tk.Frame(self.root, bg=t["bg"])
            body.pack(fill="both", expand=True, padx=self.px(32), pady=self.px(12))
            self.canvas = tk.Canvas(body, bg=t["bg"], highlightthickness=0)
            self.canvas.pack(fill="both", expand=True)
            self.inner = tk.Frame(self.canvas, bg=t["bg"])
            self.window = self.canvas.create_window(0, 0, window=self.inner, anchor="n")
            self.canvas.bind("<Configure>", self.layout)
            self.inner.bind("<Configure>", self.layout)
            for event in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                self.root.unbind(event)
            self.layout_scale = content_scale
        else:
            for w in self.inner.winfo_children():
                w.destroy()
            self.words_label = None
            self.repaint(t)
        self.built_gen = self.gen
        getattr(self, "draw_" + self.screen)(self.inner)
        slow = (time.perf_counter() - started) * 1000
        if slow > SLOW_REBUILD_MS:
            # Visible on the device when the layout cannot keep up: paste this line.
            print(f"UI: rebuilding the {self.screen} screen took {slow:.0f} ms", flush=True)
        if self.a11y_open:
            self.draw_a11y()
        self.root.after_idle(self.fit)
        if self.screen == "listen" and self.state == "listening":
            self.root.after(33, self.animate_pause_background, self.flow, self.gen)

    def repaint(self, t):
        """Recolour the widgets we kept for theme t. Processing and sent tint the whole page, and a
        refresh only retitles its own widgets, so without this the chrome kept the old colour."""
        old, self.painted = self.painted, t
        swap = {old[k]: t[k] for k in t if old[k] != t[k]}
        if not swap:
            return
        def walk(w):
            for option in ("bg", "activebackground"):
                try:
                    colour = w.cget(option)
                except tk.TclError:
                    continue
                if colour in swap:
                    w.configure({option: swap[colour]})
            for child in w.winfo_children():
                walk(child)
        walk(self.root)

    def refresh(self):
        """Reconfigure the current screen in place; screens without a refresh redraw instead."""
        fn = getattr(self, "refresh_" + self.screen, None)
        if fn is None:
            self.render(force=True)
            return
        fn()

    def fit_key(self):
        """What the fitted scale depends on. Never the state or the content, so a fit cannot
        trigger itself; the feedback survey is much taller than the call flow and fits apart."""
        return ((self.root.winfo_width(), self.root.winfo_height()), self.large, i18n.language,
                self.screen == "feedback")

    def fit(self):
        # One scale for the whole call flow (home, listen, type), chosen so the largest of its
        # screens fits the window. Fitting each screen on its own changed the scale on every CALL
        # and Cancel, and each change is a full rebuild: the page zoomed, and the torn-down
        # windows showed as blank blocks.
        if self.built_gen != self.gen or not self.canvas.winfo_exists(): return
        key = self.fit_key()
        flow = self.scale_for(key[:3] + (False,))
        content = self.scale_for(key) if key[3] else flow
        if any(new is not None and abs(new - old) > .005
               for new, old in ((flow, self.chrome_scale), (content, self.layout_scale))):
            self.render()

    def scale_for(self, key):
        """The content scale that fits key, measured once; None while the window is not laid out."""
        window, survey = key[0], key[3]
        if key in self.fitted or min(window) <= MIN_CANVAS or not self.canvas.winfo_exists():
            return self.fitted.get(key)
        self.fitted[key] = self.layout_scale   # claimed first: the idle pass below can run a queued fit
        # Nested pack sizes settle over several idle passes; measuring after only the first one
        # read a page smaller than it is and fitted a scale that clipped the bottom of it.
        self.root.update_idletasks()
        available = (self.canvas.winfo_width(), self.canvas.winfo_height()) if self.canvas.winfo_exists() else (0, 0)
        flow = self.scale_for(key[:3] + (False,)) if survey else None
        if min(available) <= MIN_CANVAS or (survey and flow is None):
            del self.fitted[key]
            return None
        need = self.largest_screen(("feedback",) if survey else ("home", "listen", "type"))
        # The header and margins, unscaled. They scale with the call flow, so for the call flow
        # solve for them too; for the survey they are fixed at the call flow's scale. FIT_SLACK
        # covers the fixed-pixel borders that do not shrink with the layout.
        chrome = [(window[i] - available[i]) / self.chrome_scale for i in (0, 1)]
        if survey:
            target = min(1.0, *((window[i] - 8 - chrome[i] * flow) / need[i] for i in (0, 1)))
        else:
            target = min(1.0, *((window[i] - 8) / (need[i] + chrome[i]) for i in (0, 1)))
        self.fitted[key] = max(MIN_LAYOUT_SCALE, min(1.0, FIT_SLACK * target))
        return self.fitted[key]

    def largest_screen(self, screens):
        """Unscaled (width, height) of the largest of these screens in any state, built off screen."""
        saved, focus = dict(self.__dict__), self.root.focus_get()
        probe = tk.Frame(self.root)    # never mapped: sizes are computed, nothing is drawn
        need = [1, 1]
        def measure():
            self.root.update_idletasks()
            need[0] = max(need[0], probe.winfo_reqwidth())
            need[1] = max(need[1], probe.winfo_reqheight())
        try:
            # Every optional row showing: rate, open request, notices.
            self.request_id, self.feedback_done, self.open_here = "fit", set(), True
            self.staff_notice, self.feedback_notice = tr("staff_failed"), tr("feedback_invalid")
            self.feedback_busy, self.words, self.keyboard = False, DEMO_SPEECH, False
            for screen in screens:
                self.screen, self.state = screen, "listening"
                for w in probe.winfo_children():
                    w.destroy()
                getattr(self, "draw_" + screen)(probe)
                if screen == "listen":
                    for state in ("speaking", "listening", "processing", "error", "sent", "empty"):
                        self.state = state
                        self.refresh_listen()
                        measure()
                else:
                    measure()
        finally:
            probe.destroy()
            self.__dict__.clear()
            self.__dict__.update(saved)
            if focus and focus.winfo_exists():
                focus.focus_set()
        return need[0] / self.layout_scale, need[1] / self.layout_scale

    def layout(self, event=None):
        if self.built_gen != self.gen or not self.canvas.winfo_exists(): return
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        needed = self.inner.winfo_reqheight()
        self.canvas.coords(self.window, w / 2, max(0, (h - needed) / 2))
        if event is not None and event.widget is self.canvas and w > 10 and h > 10:
            # Debounced: a resize fires <Configure> before the canvas has its new size, and
            # measuring the old one leaves the layout fitted to the wrong space.
            self.root.after(60, self.fit)

    def a11y_button(self, parent):
        t = self.t
        holder = tk.Frame(parent, bg=t["ink"], padx=2, pady=2)
        box = tk.Frame(holder, bg=t["bg"], padx=self.px(14), pady=self.px(8))
        box.pack()
        s = self.px(36)
        c = tk.Canvas(box, width=s, height=s, bg=t["bg"], highlightthickness=0)
        ink, w = t["ink"], max(2, s // 14)
        c.create_oval(s * .42, s * .04, s * .58, s * .2, fill=ink, outline="")
        c.create_line(s * .15, s * .32, s * .5, s * .4, s * .85, s * .32, fill=ink, width=w, capstyle="round")
        c.create_line(s * .5, s * .4, s * .5, s * .62, fill=ink, width=w)
        c.create_line(s * .32, s * .95, s * .5, s * .62, s * .68, s * .95, fill=ink, width=w, capstyle="round")
        c.pack(side="left")
        self.label(box, tr("accessibility"), 18, True).pack(side="left", padx=(self.px(8), 0))
        self.clickable(holder, self.open_a11y)
        return holder

    def draw_home(self, f):
        t = self.t
        d = self.px(390 * (1.15 if self.large else 1))
        c = tk.Canvas(f, width=d, height=d, bg=t["bg"], highlightthickness=0, cursor="hand2")
        c.create_oval(0, 0, d, d, fill=t["halo"], outline="")
        m = self.px(22)
        c.create_oval(m, m, d - m, d - m, fill=t["call"], outline="")
        c.create_text(d / 2, d / 2, text=tr("call"), fill=t["on"], font=self.font(78, True))
        c.bind("<Button-1>", lambda e: self.start_call())
        c.pack()
        self.label(f, tr("press_to_call"), 22, color="muted").pack(pady=(self.px(22), 0))
        if self.request_id and self.request_id not in self.feedback_done:
            self.button(f, tr("rate_last_service"), self.open_feedback, size=19).pack(pady=self.px(12))
        if self.open_here:
            self.label(f, tr("open_requests"), 17, color="muted").pack(pady=(self.px(10), 0))
            self.staff_button(f).pack(pady=self.px(8))
        if self.staff_notice:
            self.label(f, self.staff_notice, 17, color="muted", wrap=700).pack(pady=self.px(6))

    def draw_listen(self, f):
        # Built once with every widget any state can use, then refresh_listen() shows/hides and
        # retitles them. Rebuilding this per state meant 15 widgets unmapped and remapped for a
        # change of one sentence.
        t = self.t
        self.listen_title = self.label(f, "", 34, True, wrap=860)
        self.mic_canvas, self.mic_disc = self.draw_mic(f, active=False)
        self.listen_hint = self.label(f, "", 22)
        # Reserved box: a growing transcript used to reflow the page and push Cancel off screen.
        # Height is a calibration knob -- three lines of the 28pt words font, at either text size.
        self.listen_box = tk.Frame(f, bg=t["soft"], padx=self.px(28), pady=self.px(20),
                                   highlightbackground=t["line"], highlightthickness=2,
                                   width=self.px(706), height=self.px(200 * (1.85 / 1.6 if self.large else 1)))
        self.listen_box.pack_propagate(False)
        self.words_label = tk.Label(self.listen_box, text="", font=self.font(28), fg=t["muted"],
                                    bg=t["soft"], wraplength=self.px(650), width=0, justify="center")
        self.words_label.pack()
        self.listen_actions = tk.Frame(f, bg=t["bg"])
        self.listen_submit = self.button(self.listen_actions, tr("submit_request"), self.send_empty, size=24)
        self.listen_retry = self.button(self.listen_actions, tr("try_again"), self.ask, size=24)
        self.listen_rate_note = self.label(self.listen_actions, tr("rate_note"), 17, color="muted")
        self.listen_rate = self.button(self.listen_actions, tr("rate_service"), self.open_feedback, size=20)
        self.listen_staff = self.staff_button(self.listen_actions)
        self.listen_another = self.button(self.listen_actions, tr("another_request"), self.cancel, size=22)
        self.listen_leave = self.button(self.listen_actions, "✕   " + tr("cancel"), self.cancel, size=24)
        self.refresh_listen()

    def refresh_listen(self):
        t = self.t
        st = self.state
        self.listen_title.configure(text=tr({"speaking": "state_concern",
                                             "listening": "state_concern",
                                             "paused": "state_concern",
                                             "processing": "sending",
                                             "error": "not_confirmed",
                                             "sent": "sent",
                                             "empty": "empty_detected",
                                             "nothing": "nothing_sent"}[st]), fg=t["ink"], bg=t["bg"])
        hint = tr({"speaking": "please_wait",
                   "listening": "stop_to_send",
                   "paused": "paused",
                   "processing": "saving_message" if self.keyboard else "processing_recording",
                   "error": "error_hint",
                   "sent": "help_coming",
                   "empty": "submit_question",
                   "nothing": "could_not_hear"}[st])
        self.listen_hint.configure(text=hint, font=self.font(22, st == "listening"),
                                   fg=t["listen"] if st == "listening" else t["muted"], bg=t["bg"])
        self.mic_canvas.itemconfigure(self.mic_disc,
                                      fill=t["listen"] if st == "listening" else blend(t["listen"], t["bg"], .45))
        self.words_label.configure(
            text=tr("no_words") if st == "empty" else self.words or tr("words_here"),
            font=self.font(28, bool(self.words)), fg=t["ink"] if self.words else t["muted"])
        # Re-pack in reading order: pack() appends, so a partial re-pack would reorder the page.
        for w in (self.listen_title, self.mic_canvas, self.listen_hint, self.listen_box,
                  self.listen_actions, self.listen_submit, self.listen_retry, self.listen_rate_note,
                  self.listen_rate, self.listen_staff, self.listen_another, self.listen_leave):
            w.pack_forget()
        self.listen_title.pack()
        if self.screen == "listen" and not self.keyboard and st in ("speaking", "listening", "paused"):
            self.mic_canvas.pack(pady=self.px(4))
        if hint:
            self.listen_hint.pack()
        self.listen_box.pack(pady=(self.px(18), 0))
        self.listen_actions.pack()
        if st == "empty":
            self.listen_submit.pack(pady=self.px(8))
            self.listen_retry.pack(pady=self.px(8))
        if st == "sent" and self.request_id and self.request_id not in self.feedback_done:
            self.listen_rate_note.pack()
            self.listen_rate.pack(pady=self.px(8))
        if st == "sent":
            # Leaving this screen must not resolve the request: only staff, with a deliberate hold.
            self.listen_staff.pack(pady=(self.px(16), 0))
            self.listen_another.pack(pady=(self.px(10), 0))
        else:
            self.listen_leave.pack(pady=(self.px(16), 0))

    def on_pause_progress(self, flow, progress):
        if flow!=self.flow or self.state!='listening':return
        if progress<=0:self.pause_started=None
        elif self.pause_started is None:
            self.pause_started=time.monotonic()-min(1,progress)*2

    def animate_pause_background(self, flow, generation):
        if flow!=self.flow or generation!=self.gen or self.state!='listening':return
        # Animate on the UI clock, independently of the microphone's 100 ms blocks.
        target=0.0 if self.pause_started is None else min(.98,(time.monotonic()-self.pause_started)/2)
        self.pause_display+=(target-self.pause_display)*.4
        if target==0 and self.pause_display<.005:self.pause_display=0
        if not self.a11y_open and (self.pause_display>0 or self.background_sweep is not None):
            if self.background_sweep is None:
                from background_sweep import BackgroundSweep
                self.root.update_idletasks()
                self.background_sweep=BackgroundSweep(self.root,self.t['bg'],'#D8F3DC')
            self.background_sweep.update(self.pause_display)
        self.root.after(33,self.animate_pause_background,flow,generation)

    def on_volume(self, flow, level):
        if flow!=self.flow or self.state!='listening' or not self.mic_visual:return
        canvas,cx,cy,r=self.mic_visual
        if not canvas.winfo_exists():return
        canvas.delete('ring')
        if level<=0:return
        import math
        strength=max(0,min(1,math.log1p(level/220)/math.log1p(20)))
        for offset,fade in ((12,.15),(25,.55)):
            radius=r+self.px(offset+strength*18)
            canvas.create_oval(cx-radius,cy-radius,cx+radius,cy+radius,
                outline=blend(self.t['listen'],self.t['bg'],fade),width=self.px(4),tags='ring')
        canvas.tag_raise('mic')

    def draw_mic(self, f, active):
        """Draws the microphone; the caller packs it, and refresh_listen() recolours it."""
        t = self.t
        size = self.px(230)
        c = tk.Canvas(f, width=size, height=size, bg=t["bg"], highlightthickness=0)
        r = self.px(70)
        cx = cy = size / 2
        fill = t["listen"] if active else blend(t["listen"], t["bg"], .45)
        disc = c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline="", tags="mic")
        u, w = self.px(1), max(3, self.px(5))
        c.create_rectangle(cx - 14 * u, cy - 40 * u, cx + 14 * u, cy + 8 * u, fill=t["on"], outline="", tags="mic")
        c.create_oval(cx - 14 * u, cy - 54 * u, cx + 14 * u, cy - 26 * u, fill=t["on"], outline="", tags="mic")
        c.create_oval(cx - 14 * u, cy - 6 * u, cx + 14 * u, cy + 22 * u, fill=t["on"], outline="", tags="mic")
        c.create_arc(cx - 30 * u, cy - 26 * u, cx + 30 * u, cy + 34 * u, start=180, extent=180,
                     style="arc", outline=t["on"], width=w, tags="mic")
        c.create_line(cx, cy + 34 * u, cx, cy + 50 * u, fill=t["on"], width=w, tags="mic")
        self.mic_visual = (c, cx, cy, r)
        return c, disc

    def draw_type(self, f):
        t = self.t
        self.label(f, tr("type_need"), 32, True).pack()
        box = tk.Frame(f, bg=t["line"], padx=2, pady=2)
        box.pack(fill="x", pady=(self.px(16), 0))
        entry = tk.Entry(box, textvariable=self.typed, font=self.font(26), bg=t["soft"], fg=t["ink"],
                         insertbackground=t["ink"], relief="flat", bd=self.px(10), width=36)
        entry.pack(fill="x")
        entry.bind("<Return>", lambda e: self.send_typed())
        entry.focus_set()
        entry.icursor("end")

        kb = tk.Frame(f, bg=t["bg"])
        kb.pack(pady=self.px(14))
        for keys in ("QWERTYUIOP", "ASDFGHJKL", "ZXCVBNM"):
            row = tk.Frame(kb, bg=t["bg"])
            row.pack(pady=self.px(3))
            for k in keys:
                self.key(row, k, lambda ch=k: self.type_char(ch)).pack(side="left", padx=self.px(3))
        row = tk.Frame(kb, bg=t["bg"])
        row.pack(pady=self.px(3))
        self.key(row, tr("key_delete"), self.backspace, 7).pack(side="left", padx=self.px(3))
        self.key(row, tr("key_space"), lambda: self.type_char(" "), 14).pack(side="left", padx=self.px(3))
        self.key(row, tr("key_send"), self.send_typed, 7, send=True).pack(side="left", padx=self.px(3))

        self.button(f, "✕   " + tr("cancel"), self.cancel, size=24, danger=False).pack(pady=(self.px(10), 0))

    def key(self, parent, text, cmd, width=3, send=False):
        t = self.t
        bg, fg = (t["ok"], t["on"]) if send else (t["soft"], t["ink"])
        return tk.Button(parent, text=text, command=cmd, font=self.font(19, True), width=width,
                         bg=bg, fg=fg, activebackground=t["line"] if not send else bg,
                         activeforeground=fg, relief="flat", bd=0, pady=self.px(8),
                         cursor="hand2", highlightthickness=0)

    def draw_a11y(self):
        t = self.t
        cover = tk.Frame(self.root, bg=blend(t["ink"], t["bg"], .55))
        cover.place(relx=0, rely=0, relwidth=1, relheight=1)
        card = tk.Frame(cover, bg=t["bg"], padx=self.px(28), pady=self.px(22),
                        highlightbackground=t["line"], highlightthickness=2)
        card.place(relx=.5, rely=.5, anchor="center")

        head = tk.Frame(card, bg=t["bg"])
        head.pack(fill="x", pady=(0, self.px(18)))
        if self.lang_open:
            self.button(head, "✕  " + tr("exit"), self.close_languages, size=19).pack(side="left")
            self.label(head, tr("language"), 28, True).pack(side="left", padx=self.px(18))
            self.draw_languages(card)
            return
        self.button(head, "✕  " + tr("exit"), self.close_a11y, size=19).pack(side="left")
        self.label(head, tr("accessibility"), 28, True).pack(side="left", padx=self.px(18))

        row = tk.Frame(card, bg=t["bg"])
        row.pack()
        on, off = tr("on"), tr("off")
        tiles = [
            ("keyboard", tr("keyboard"), on if self.keyboard else off, self.keyboard, self.toggle_keyboard),
            ("colour", tr("colour_filter"), tr(FILTERS[self.filter_i][1]), self.filter_i != 0, self.next_filter),
            ("text", tr("larger_text"), on if self.large else off, self.large, self.toggle_large),
            ("language", tr("language"), i18n.NAMES[i18n.language], i18n.language != "en", self.open_languages),
        ]
        for i, tile in enumerate(tiles):
            self.a11y_tile(row, *tile).grid(row=0, column=i, padx=self.px(8), sticky="nsew")
            row.columnconfigure(i, weight=1, uniform="a")

    def a11y_tile(self, parent, icon, name, state, active, cmd):
        t = self.t
        bg = t["soft"] if active else t["bg"]
        holder = tk.Frame(parent, bg=t["listen"] if active else t["line"], padx=3, pady=3)
        box = tk.Frame(holder, bg=bg, padx=self.px(22), pady=self.px(18))
        box.pack(fill="both", expand=True)
        s = self.px(64)
        c = tk.Canvas(box, width=s, height=s, bg=bg, highlightthickness=0)
        ink, w = t["ink"], max(2, s // 20)
        if icon == "keyboard":
            c.create_rectangle(s * .06, s * .24, s * .94, s * .76, outline=ink, width=w)
            for y in (.4, .56):
                for x in (.2, .35, .5, .65, .8):
                    c.create_rectangle(s * x - 2, s * y - 2, s * x + 2, s * y + 2, fill=ink, outline=ink)
            c.create_line(s * .3, s * .67, s * .7, s * .67, fill=ink, width=w)
        elif icon == "colour":
            c.create_oval(s * .1, s * .1, s * .9, s * .9, outline=ink, width=w)
            c.create_arc(s * .1, s * .1, s * .9, s * .9, start=-90, extent=180, fill=ink, outline=ink)
        elif icon == "language":
            # A globe: outline, one meridian and two parallels.
            c.create_oval(s * .1, s * .1, s * .9, s * .9, outline=ink, width=w)
            c.create_oval(s * .33, s * .1, s * .67, s * .9, outline=ink, width=w)
            for y in (.37, .63):
                c.create_line(s * .14, s * y, s * .86, s * y, fill=ink, width=w)
        else:
            c.create_text(s * .3, s * .62, text="A", fill=ink, font=(self.family, -int(s * .4), "bold"))
            c.create_text(s * .68, s * .55, text="A", fill=ink, font=(self.family, -int(s * .7), "bold"))
        c.pack()
        tk.Label(box, text=name, font=self.font(21, True), fg=t["ink"], bg=bg).pack(pady=(self.px(10), 0))
        tk.Label(box, text=state, font=self.font(17, active), fg=t["listen"] if active else t["muted"],
                 bg=bg).pack()
        self.clickable(holder, cmd)
        return holder

    # ---- actions
    def start_call(self):
        if self.a11y_open:
            return
        self.staff_notice = ""
        self.flow += 1
        self.words = ""
        self.tries = 0
        if self.keyboard:
            self.typed.set("")
            self.screen = "type"
            self.render()
            self.voice.say("Type what you need.")
        else:
            self.ask()

    def ask(self):
        self.flow += 1
        f = self.flow
        self.screen, self.state = "listen", "speaking"
        # begin_listen() draws this screen again one call later inside the same frame, so the
        # "Please wait" paint was never visible -- it was a second full rebuild of the UI thread.
        self.begin_listen(f)

    def begin_listen(self, f):
        if f != self.flow:
            return
        self.state = "listening"
        self.render()
        self.voice.listen(lambda text: self.on_words(f, text), lambda text, rid=None: self.on_done(f, text, rid),
                          lambda error: self.on_error(f, error), lambda: self.on_processing(f),
                          lambda progress: self.on_pause_progress(f, progress),
                          lambda level: self.on_volume(f, level))

    def on_processing(self, f):
        if f == self.flow:
            self.state = "processing"
            self.render()

    def on_error(self, f, error):
        if f == self.flow:
            print("Patient request error:", error, file=sys.stderr)
            self.words = (tr("mic_unavailable")
                          if any(word in error.lower() for word in ('microphone', 'portaudio', 'input device'))
                          else tr("save_failed"))
            self.state = "error"
            self.render()

    def on_words(self, f, text):
        if f != self.flow or not text:
            return
        self.words = text[0].upper() + text[1:]
        if self.words_label and self.words_label.winfo_exists():
            self.words_label.configure(text=self.words, fg=self.t["ink"], font=self.font(28, True))

    def on_done(self, f, text, request_id=None):
        if f != self.flow:
            return
        if text:
            self.request_id = request_id
            self.words = text[0].upper() + text[1:]
            self.open_here = True
            self.staff_notice = ""
            self.state = "sent"
            self.render()
        else:
            self.words=""
            self.state="empty"
            self.render()
            self.voice.say("Empty request detected. Do you want to submit?")

    def send_empty(self):
        if self.state != 'empty':return
        self.voice.stop()
        self.flow+=1
        flow=self.flow
        self.state='processing'
        self.render()
        def work():
            try:
                result=submit_empty(f"{ROOM} · {BED}")
                self.voice.q.put((self.on_done,(flow,result['transcript'],result['request_id'])))
            except Exception as error:self.voice.q.put((self.on_error,(flow,str(error))))
        threading.Thread(target=work,daemon=True).start()

    def send_typed(self):
        msg = self.typed.get().strip()
        if not msg or self.screen != "type":
            return
        self.flow += 1
        flow = self.flow
        self.words = msg
        self.screen, self.state = "listen", "processing"
        self.render()
        def work():
            try:
                if self.voice.demo:
                    result = {'transcript': msg, 'request_id': None}
                else:
                    result = submit_text(msg, f"{ROOM} · {BED}")
                self.voice.q.put((self.on_done, (flow, result['transcript'], result['request_id'])))
            except Exception as error:
                self.voice.q.put((self.on_error, (flow, str(error))))
        threading.Thread(target=work, daemon=True).start()

    def open_feedback(self):
        if not self.request_id or self.request_id in self.feedback_done:
            return
        self.voice.stop()
        self.rating.set(0)
        self.resolution.set("")
        self.comment.set("")
        self.feedback_notice = ""
        self.screen = "feedback"
        self.render()

    def draw_feedback(self, f):
        self.label(f, tr("feedback_title"), 30, True, wrap=800).pack()
        row = tk.Frame(f, bg=self.t['bg']); row.pack()
        self.rating_cards=[]
        # Draw faces directly so Raspberry Pi does not need an emoji font.
        for n, title in enumerate([tr(k) for k in ('very_unhappy', 'unhappy', 'okay', 'happy', 'very_happy')], 1):
            card=tk.Frame(row,bg=self.t['bg'],highlightthickness=self.px(3),highlightbackground=self.t['bg']);card.grid(row=0,column=n-1,padx=self.px(7),sticky='n')
            row.columnconfigure(n-1,weight=1,uniform='ratings')
            d=self.px(94)
            face=tk.Canvas(card,width=d,height=d,bg=self.t['bg'],highlightthickness=0)
            colour=('#DF1D2E','#EF7523','#F2AD3E','#90BF40','#48AB4D')[n-1]
            face.create_oval(5,5,d-5,d-5,fill=self.t['bg'],outline=colour,width=self.px(6),tags='face_disc')
            for x in (.34,.66):
                face.create_oval(d*x-3,d*.34-3,d*x+3,d*.34+3,fill=colour,outline='')
            if n<3:
                face.create_arc(d*.27,d*(.53 if n==1 else .60),d*.73,d*(.89 if n==1 else .79),start=0,extent=180,style='arc',outline=colour,width=self.px(5))
            elif n==3:face.create_line(d*.3,d*.65,d*.7,d*.65,fill=colour,width=self.px(5),capstyle='round')
            else:face.create_arc(d*.25,d*(.40 if n==5 else .49),d*.75,d*.76,start=180,extent=180,style='arc',outline=colour,width=self.px(5))
            face.pack()
            stars=self.label(card,'★'*n,17,True);stars.configure(fg=colour);stars.pack()
            face.bind('<Button-1>',lambda e,value=n: self.select_rating(value))
            face.configure(cursor='hand2')
            self.label(card,str(n),26,True).pack()
            self.label(card,title,17,wrap=150).pack()
            self.rating_cards.append((n,card,face,colour))
            for target in [card]+[w for w in card.winfo_children() if not isinstance(w,tk.Radiobutton)]:
                target.bind('<Button-1>',lambda e,value=n:self.select_rating(value))
                target.configure(cursor='hand2')
        self.refresh_rating_highlight()
        self.label(f, tr("resolved_question"), 27, True).pack(pady=(self.px(20), 0))
        row = tk.Frame(f, bg=self.t['bg']); row.pack()
        self.resolution_cards=[]
        for value, title in [('yes', tr('yes')), ('no', tr('no'))]:
            card=tk.Frame(row,bg=self.t['bg'],highlightthickness=self.px(3),highlightbackground=self.t['bg'])
            card.pack(side='left',padx=self.px(10))
            button=tk.Button(card,text=title,font=self.font(26,True),
                             command=lambda answer=value:self.select_resolution(answer),
                             bg=self.t['bg'],fg=self.t['ink'],relief='flat',bd=0,
                             padx=self.px(24),pady=self.px(8),highlightthickness=0,
                             state='disabled' if self.feedback_busy else 'normal',cursor='hand2')
            button.pack()
            self.resolution_cards.append((value,card,button))
        self.refresh_resolution_highlight()
        self.label(f, tr("comment_optional"), 22).pack(pady=(self.px(14), 0))
        tk.Entry(f, textvariable=self.comment, width=32, font=self.font(22),
                 state='disabled' if self.feedback_busy else 'normal').pack()
        self.label(f, self.feedback_notice, 17, color="muted", wrap=700).pack(pady=self.px(10))
        if not self.feedback_busy:
            actions=tk.Frame(f,bg=self.t['bg']);actions.pack(pady=self.px(8))
            if self.request_id not in self.feedback_done:
                self.button(actions, tr("submit_feedback"), self.send_feedback, size=23).pack(side='left',padx=self.px(8))
            self.button(actions, tr("done") if self.request_id in self.feedback_done else tr("skip"), self.cancel, size=22).pack(side='left',padx=self.px(8))

    def select_rating(self, value):
        if self.feedback_busy:return
        self.rating.set(value)
        self.refresh_rating_highlight()

    def refresh_rating_highlight(self):
        for value,card,face,colour in self.rating_cards:
            chosen=self.rating.get()==value
            bg=blend(colour,self.t['bg'],.84) if chosen else self.t['bg']
            card.configure(bg=bg,highlightbackground=colour if chosen else self.t['bg'])
            for child in card.winfo_children():
                child.configure(bg=bg)
                if isinstance(child,tk.Radiobutton):child.configure(selectcolor=bg,activebackground=bg)
            face.itemconfigure('face_disc',fill=colour if chosen else self.t['bg'])
            for item in face.find_all():
                if 'face_disc' in face.gettags(item):continue
                ink='#FFFFFF' if chosen else colour
                if face.type(item)=='arc':face.itemconfigure(item,outline=ink)
                else:face.itemconfigure(item,fill=ink)

    def select_resolution(self, answer):
        if self.feedback_busy:return
        self.resolution.set(answer)
        self.refresh_resolution_highlight()

    def refresh_resolution_highlight(self):
        for answer,card,button in self.resolution_cards:
            chosen=self.resolution.get()==answer
            bg=self.t['soft'] if chosen else self.t['bg']
            card.configure(bg=bg,highlightbackground=self.t['ink'] if chosen else self.t['bg'])
            button.configure(bg=bg,activebackground=bg)

    def send_feedback(self):
        if self.feedback_busy or self.request_id in self.feedback_done:
            return
        rating, answer, comment = self.rating.get(), self.resolution.get(), self.comment.get().strip()
        if rating not in range(1, 6) or answer not in ('yes', 'no') or len(comment) > 2000:
            self.feedback_notice = tr("feedback_invalid")
            self.render()
            return
        rid = self.request_id
        self.feedback_busy = True
        self.feedback_notice = tr("saving_feedback")
        self.render()
        def work():
            try:
                submit_feedback(rid, rating, answer, comment)
                self.voice.q.put((self.feedback_result, (rid, None)))
            except Exception as error:
                self.voice.q.put((self.feedback_result, (rid, str(error))))
        threading.Thread(target=work, daemon=True).start()

    def feedback_result(self, rid, error):
        self.feedback_busy = False
        if error is None:
            self.feedback_done.add(rid)
        self.feedback_notice = error or tr("feedback_saved")
        if self.screen == "feedback" and self.request_id == rid:
            self.render()

    def type_char(self, ch):
        v = self.typed.get()
        self.typed.set(v + (ch.upper() if not v.strip() else ch.lower()))

    def backspace(self):
        self.typed.set(self.typed.get()[:-1])

    def cancel(self):
        self.flow += 1
        self.voice.stop()
        self.words = ""
        self.screen = "home"
        self.render()
        self.voice.say("Recording closed.")

    # ---- staff: done at the bedside
    def staff_button(self, parent):
        """Press and hold for HOLD_MS. A tap does nothing, so the response time stays honest."""
        t = self.t
        holder = tk.Frame(parent, bg=t["ink"], padx=2, pady=2)
        face = tk.Label(holder, text=tr("staff_done"), font=self.font(20, True), bg=t["bg"], fg=t["ink"],
                        padx=self.px(40), pady=self.px(14), cursor="hand2")
        face.pack()
        face.bind("<ButtonPress-1>", lambda e: self.hold_start(face))
        face.bind("<ButtonRelease-1>", lambda e: self.hold_cancel(face))
        face.bind("<Leave>", lambda e: self.hold_cancel(face))
        return holder

    def hold_start(self, face):
        if self.resolving:
            return
        self.hold_cancel(face)
        face.configure(text=tr("staff_hold"), bg=self.t["line"])
        self.hold_job = self.root.after(HOLD_MS, lambda: self.staff_done(face))

    def hold_cancel(self, face):
        if self.hold_job:
            self.root.after_cancel(self.hold_job)
            self.hold_job = None
        if not self.resolving and face.winfo_exists():
            face.configure(text=tr("staff_done"), bg=self.t["bg"])

    def staff_done(self, face):
        self.hold_job = None
        self.resolving = True
        face.configure(text="…")
        room = f"{ROOM} · {BED}"
        self.background(lambda: None if self.voice.demo else resolve_room(room), self.staff_result,
                        lambda error: self.staff_result(None, error))

    def staff_result(self, _, error=None):
        self.resolving = False
        if error:
            print("Could not mark requests done:", error, file=sys.stderr)
            self.staff_notice = tr("staff_failed")
        else:
            self.open_here = False
            self.staff_notice = tr("staff_saved")
            self.flow += 1
            self.voice.stop()
            self.words = ""
            self.screen = "home"
        self.render()

    def set_open(self, count):
        if bool(count) != self.open_here:
            self.open_here = bool(count)
            if self.screen == "home":
                self.render()

    def background(self, work, done, failed=None):
        """Run work() off the UI thread; done(result) or failed(message) run back on it."""
        def run():
            try:
                self.voice.q.put((done, (work(),)))
            except Exception as error:
                if failed:
                    self.voice.q.put((failed, (str(error),)))
                else:
                    print("Background request failed:", error, file=sys.stderr)
        threading.Thread(target=run, daemon=True).start()

    # ---- screen language
    def open_languages(self):
        self.lang_open = True
        self.render()

    def close_languages(self):
        self.lang_open = False
        self.render()

    def draw_languages(self, card):
        t = self.t
        box = tk.Frame(card, bg=t["bg"])
        box.pack(fill="both", expand=True)
        bar = tk.Scrollbar(box, width=self.px(36))
        listbox = tk.Listbox(box, font=self.font(19), height=7, width=26, activestyle="none",
                             bg=t["bg"], fg=t["ink"], selectbackground=t["listen"], selectforeground=t["on"],
                             highlightthickness=2, highlightbackground=t["line"], relief="flat",
                             yscrollcommand=bar.set, exportselection=False)
        bar.configure(command=listbox.yview)
        for code, name in i18n.LANGUAGES:
            listbox.insert("end", name)
        current = [code for code, _ in i18n.LANGUAGES].index(i18n.language)
        listbox.selection_set(current)
        listbox.see(current)
        listbox.pack(side="left", fill="both", expand=True)
        bar.pack(side="left", fill="y")
        listbox.bind("<<ListboxSelect>>", lambda e: listbox.curselection() and
                     self.choose_language(i18n.LANGUAGES[listbox.curselection()[0]][0]))

    def choose_language(self, code):
        if code == i18n.language:
            self.close_languages()
            return
        i18n.apply(code, i18n.local_strings(code))
        i18n.save(code)
        voice_client.UI_LANGUAGE = code
        self.lang_open = False
        self.render()
        if code != "en" and not self.voice.demo:
            self.fetch_strings(code)

    def fetch_strings(self, code):
        self.background(lambda: ui_strings(code), lambda strings: self.strings_loaded(code, strings),
                        lambda error: print(f"Screen text for {code} unavailable ({error}); using built-in text.", file=sys.stderr))

    def strings_loaded(self, code, strings):
        if code != i18n.language:
            return
        i18n.apply(code, strings)
        i18n.save(code, strings)
        self.fitted.clear()      # same language, different text: it may wrap to more lines
        self.render(force=True)

    # ---- accessibility
    def open_a11y(self):
        if self.screen == "listen" and self.state in ("speaking", "listening"):
            self.flow += 1
            self.voice.stop()
            self.state = "paused"
        self.a11y_open = True
        self.render()

    def close_a11y(self):
        self.a11y_open = False
        self.lang_open = False
        if self.screen == "listen" and self.state == "paused":
            self.ask()
        else:
            self.render()

    def toggle_keyboard(self):
        self.keyboard = not self.keyboard
        if self.keyboard and self.screen == "listen" and self.state == "paused":
            self.typed.set(self.words)
            self.screen = "type"
        elif not self.keyboard and self.screen == "type":
            self.screen, self.state = "listen", "paused"
        self.render()

    def next_filter(self):
        self.filter_i = (self.filter_i + 1) % len(FILTERS)
        self.render()

    def toggle_large(self):
        self.large = not self.large
        self.render()

    # ---- keys
    def on_escape(self, _):
        if self.lang_open:
            self.close_languages()
        elif self.a11y_open:
            self.close_a11y()
        else:
            self.root.attributes("-fullscreen", False)

    def on_call_key(self, _):
        if self.screen == "home" and not self.a11y_open:
            self.start_call()


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    if "--demo" not in sys.argv:
        voice_client.start_transport()   # over Bluetooth, start advertising before the first call
    root = tk.Tk()
    App(root, demo="--demo" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
