"""
Bedside Nurse Call - patient screen (prototype)

Run:   python nurse_call.py
Demo:  python nurse_call.py --demo    (pretends the patient spoke; no microphone needed)

Keys:  F11 full screen  |  Esc closes the menu / leaves full screen
       Space or Enter on the start screen presses CALL

Flow:  CALL  ->  "I'm listening. Tell me what you need."  ->  patient speaks,
       words appear on screen  ->  sent to the nurse.  One Cancel button.

Voice and speech recognition use what is built into Windows, so nothing needs installing.
"""
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont

ROOM, BED = "412", "B"
PROMPT = "I'm listening. Tell me what you need."
DEMO_SPEECH = "My back is hurting and I need help sitting up."

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
FILTERS = [("standard", "Standard"), ("contrast", "High contrast"), ("night", "Night (dim)")]

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
        self.windows = sys.platform == "win32"
        self.q = queue.Queue()
        self._proc = None
        self._lock = threading.Lock()

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
        with self._lock:
            p, self._proc = self._proc, None
        if p and p.poll() is None:
            p.kill()

    def say(self, text, then=None):
        def work():
            if self.windows:
                p = self._start(SAY_PS.replace("{text}", text.replace("'", "''")))
                if p:
                    p.wait()
            else:
                try:
                    import pyttsx3
                    engine = pyttsx3.init()
                    engine.say(text)
                    engine.runAndWait()
                except Exception:
                    pass
            self.q.put((then, ()))
        self.stop()
        threading.Thread(target=work, daemon=True).start()

    def listen(self, on_words, on_done):
        """on_words(text) is called as words arrive; on_done(text or None) at the end."""
        def work():
            final = None
            if self.demo:
                words = DEMO_SPEECH.split()
                for i in range(1, len(words) + 1):
                    time.sleep(0.35)
                    self.q.put((on_words, (" ".join(words[:i]),)))
                final = DEMO_SPEECH
            elif self.windows:
                p = self._start(LISTEN_PS)
                if p:
                    for raw in p.stdout:
                        line = raw.decode("utf-8", "replace").strip()
                        if line.startswith("H:"):
                            self.q.put((on_words, (line[2:],)))
                        elif line.startswith("R:"):
                            final = line[2:]
                    p.wait()
            self.q.put((on_done, (final or None,)))
        self.stop()
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

        self.screen = "home"     # home | listen | type
        self.state = "speaking"  # speaking | listening | sent | nothing   (listen screen)
        self.words = ""
        self.tries = 0
        self.keyboard = False
        self.filter_i = 0
        self.large = False
        self.a11y_open = False
        self.flow = 0            # bumps whenever a voice exchange is abandoned
        self.gen = 0             # bumps on every redraw (stops old animations)
        self.typed = tk.StringVar()
        self.words_label = None

        families = set(tkfont.families())
        self.family = next((f for f in ("Atkinson Hyperlegible", "Segoe UI", "Helvetica")
                            if f in families), "TkDefaultFont")

        root.title("Nurse Call")
        root.geometry(f"{self.px(1000)}x{self.px(740)}")
        root.minsize(self.px(820), self.px(640))
        root.bind("<F11>", lambda e: root.attributes("-fullscreen", not root.attributes("-fullscreen")))
        root.bind("<Escape>", self.on_escape)
        root.bind("<space>", self.on_call_key)
        root.bind("<Return>", self.on_call_key)

        self.render()
        self.poll()

    # ---- helpers
    @property
    def t(self):
        return THEMES[FILTERS[self.filter_i][0]]

    def px(self, n):
        return int(n * self.dpi)

    def font(self, size, bold=False):
        scale = 1.3 if self.large else 1.0
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
    def render(self):
        self.gen += 1
        self.words_label = None
        for w in self.root.winfo_children():
            w.destroy()
        t = self.t
        self.root.configure(bg=t["bg"])

        top = tk.Frame(self.root, bg=t["bg"])
        top.pack(fill="x", padx=self.px(24), pady=(self.px(16), 0))
        self.label(top, f"Room {ROOM}   ·   Bed {BED}", 17, color="muted").pack(side="left")
        self.a11y_button(top).pack(side="right")

        body = tk.Frame(self.root, bg=t["bg"])
        body.pack(fill="both", expand=True, padx=self.px(32), pady=self.px(12))
        inner = tk.Frame(body, bg=t["bg"])
        inner.place(relx=.5, rely=.5, anchor="center")
        getattr(self, "draw_" + self.screen)(inner)

        if self.a11y_open:
            self.draw_a11y()

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
        self.label(box, "Accessibility", 18, True).pack(side="left", padx=(self.px(8), 0))
        self.clickable(holder, self.open_a11y)
        return holder

    def draw_home(self, f):
        t = self.t
        d = self.px(330 * (1.15 if self.large else 1))
        c = tk.Canvas(f, width=d, height=d, bg=t["bg"], highlightthickness=0, cursor="hand2")
        c.create_oval(0, 0, d, d, fill=t["halo"], outline="")
        m = self.px(22)
        c.create_oval(m, m, d - m, d - m, fill=t["call"], outline="")
        c.create_text(d / 2, d / 2, text="CALL", fill=t["on"], font=self.font(60, True))
        c.bind("<Button-1>", lambda e: self.start_call())
        c.pack()
        self.label(f, "Press to call your nurse", 22, color="muted").pack(pady=(self.px(22), 0))

    def draw_listen(self, f):
        t = self.t
        st = self.state
        title = {"speaking": "I'm listening.",
                 "listening": "I'm listening.",
                 "paused": "I'm listening.",
                 "sent": "Your nurse has your message.",
                 "nothing": "Your nurse has been called."}[st]
        self.label(f, title, 34, True, wrap=860).pack()

        if self.screen == "listen" and not self.keyboard:
            self.draw_mic(f, active=(st == "listening"))

        hint = {"speaking": "Please wait…",
                "listening": "Tell me what you need",
                "paused": "Paused",
                "sent": "",
                "nothing": "I couldn't hear a message."}[st]
        if hint:
            self.label(f, hint, 22, st == "listening",
                       color="listen" if st == "listening" else "muted").pack()

        box = tk.Frame(f, bg=t["soft"], padx=self.px(28), pady=self.px(20),
                       highlightbackground=t["line"], highlightthickness=2)
        box.pack(pady=(self.px(18), 0))
        text = self.words or "Your words will show here"
        self.words_label = tk.Label(box, text=text, font=self.font(28, bool(self.words)),
                                    fg=t["ink"] if self.words else t["muted"], bg=t["soft"],
                                    wraplength=self.px(760), width=34, justify="center")
        self.words_label.pack()

        self.button(f, "✕   Cancel", self.cancel, size=24, danger=True).pack(pady=(self.px(30), 0))

    def draw_mic(self, f, active):
        t = self.t
        size = self.px(230)
        c = tk.Canvas(f, width=size, height=size, bg=t["bg"], highlightthickness=0)
        c.pack(pady=self.px(4))
        r = self.px(70)
        cx = cy = size / 2
        fill = t["listen"] if active else blend(t["listen"], t["bg"], .45)
        c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=fill, outline="", tags="mic")
        u, w = self.px(1), max(3, self.px(5))
        c.create_rectangle(cx - 14 * u, cy - 40 * u, cx + 14 * u, cy + 8 * u, fill=t["on"], outline="", tags="mic")
        c.create_oval(cx - 14 * u, cy - 54 * u, cx + 14 * u, cy - 26 * u, fill=t["on"], outline="", tags="mic")
        c.create_oval(cx - 14 * u, cy - 6 * u, cx + 14 * u, cy + 22 * u, fill=t["on"], outline="", tags="mic")
        c.create_arc(cx - 30 * u, cy - 26 * u, cx + 30 * u, cy + 34 * u, start=180, extent=180,
                     style="arc", outline=t["on"], width=w, tags="mic")
        c.create_line(cx, cy + 34 * u, cx, cy + 50 * u, fill=t["on"], width=w, tags="mic")
        if active:
            self.animate_rings(c, cx, cy, r, self.gen, 0.0)

    def animate_rings(self, c, cx, cy, r, g, phase):
        if g != self.gen or not c.winfo_exists():
            return
        c.delete("ring")
        t = self.t
        for k in (0.0, 0.5):
            p = (phase + k) % 1.0
            rr = r + p * self.px(44)
            c.create_oval(cx - rr, cy - rr, cx + rr, cy + rr,
                          outline=blend(t["listen"], t["bg"], p), width=self.px(4), tags="ring")
        c.tag_raise("mic")
        self.root.after(40, self.animate_rings, c, cx, cy, r, g, phase + 0.02)

    def draw_type(self, f):
        t = self.t
        self.label(f, "Type what you need", 32, True).pack()
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
        self.key(row, "Delete", self.backspace, 7).pack(side="left", padx=self.px(3))
        self.key(row, "Space", lambda: self.type_char(" "), 14).pack(side="left", padx=self.px(3))
        self.key(row, "Send", self.send_typed, 7, send=True).pack(side="left", padx=self.px(3))

        self.button(f, "✕   Cancel", self.cancel, size=24, danger=True).pack(pady=(self.px(10), 0))

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
        self.button(head, "✕  Exit", self.close_a11y, size=19).pack(side="left")
        self.label(head, "Accessibility", 28, True).pack(side="left", padx=self.px(18))

        row = tk.Frame(card, bg=t["bg"])
        row.pack()
        tiles = [
            ("keyboard", "Keyboard", "On" if self.keyboard else "Off", self.keyboard, self.toggle_keyboard),
            ("colour", "Colour filter", FILTERS[self.filter_i][1], self.filter_i != 0, self.next_filter),
            ("text", "Larger text", "On" if self.large else "Off", self.large, self.toggle_large),
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
        self.flow += 1
        self.words = ""
        self.tries = 0
        if self.keyboard:
            self.typed.set("")
            self.screen = "type"
            self.render()
            self.voice.say("I've called your nurse. Type what you need.")
        else:
            self.ask()

    def ask(self):
        self.flow += 1
        f = self.flow
        self.screen, self.state = "listen", "speaking"
        self.render()
        self.voice.say(PROMPT, then=lambda: self.begin_listen(f))

    def begin_listen(self, f):
        if f != self.flow:
            return
        self.state = "listening"
        self.render()
        self.voice.listen(lambda text: self.on_words(f, text), lambda text: self.on_done(f, text))

    def on_words(self, f, text):
        if f != self.flow or not text:
            return
        self.words = text[0].upper() + text[1:]
        if self.words_label and self.words_label.winfo_exists():
            self.words_label.configure(text=self.words, fg=self.t["ink"], font=self.font(28, True))

    def on_done(self, f, text):
        if f != self.flow:
            return
        if text:
            self.words = text[0].upper() + text[1:]
            self.state = "sent"
            self.render()
            self.voice.say("Thank you. Your nurse has your message.")
        elif self.tries == 0:
            self.tries += 1
            self.words = ""
            self.flow += 1
            f = self.flow
            self.state = "speaking"
            self.render()
            self.voice.say("I didn't hear you. Please try again.", then=lambda: self.begin_listen(f))
        else:
            self.state = "nothing"
            self.render()
            self.voice.say("Your nurse has been called.")

    def send_typed(self):
        msg = self.typed.get().strip()
        if not msg:
            return
        self.words = msg[0].upper() + msg[1:]
        self.screen, self.state = "listen", "sent"
        self.render()
        self.voice.say("Thank you. Your nurse has your message.")

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
        self.voice.say("Your call is cancelled.")

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
        if self.a11y_open:
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
    root = tk.Tk()
    App(root, demo="--demo" in sys.argv)
    root.mainloop()


if __name__ == "__main__":
    main()
