"""Shared pieces of the staff apps: sign-in, the Care Queue look, background work and small widgets.

The colours, type sizes and wording follow the original clerk dashboard and department inbox pages.
"""
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from bello_api import ApiError, Client, Unauthorized

HERE = Path(__file__).resolve().parent
TEAMS = ['Primary Nurse', 'Housekeeping & Facilities', 'Food Services', 'Unit Clerk / IT Maintenance']

# The original Care Queue palette.
PAGE = '#e5ebf2'           # page background
SURFACE = '#ffffff'        # panel
STRIPE = '#f5f8fc'         # every other row
TOOLBAR = '#f3f6fa'
TH_BG, TH_INK = '#dde7f1', '#344d66'
BORDER, ROW_LINE = '#b8c7d6', '#dfe6ee'
HEAD, HEAD_EDGE, HEAD_INK = '#24476b', '#183754', '#e3edf8'
INK, INK_2, MUTED, EYEBROW = '#243448', '#3c536d', '#62758a', '#59718c'
LINK = '#224f7e'
COUNT_BG, COUNT_INK, COUNT_EDGE = '#f8fbff', '#284e76', '#b9cadb'
CRITICAL_BG, CRITICAL_EDGE = '#fff4f3', '#b64343'
FLAG_BG, FLAG_INK = '#fff0d2', '#815211'
CALL_BG, CALL_EDGE = '#285d8d', '#204c74'
DISPATCH_BG, DISPATCH_EDGE, DISPATCH_INK = '#eef4fa', '#9bb5ce', '#234f79'
DIALOG_HEAD = '#eef3f9'
BADGES = {'urgent': ('#fce9e8', '#ac3836'), 'high': ('#fff1dd', '#98631e'),
          'routine': ('#e7eef7', '#3c5d83'), 'low': ('#edf1f4', '#5c6e7d'), 'none': ('#f1f3f5', '#6b7680')}
SERIES, GRID, AXIS = '#2a78d6', '#e1e0d9', '#c3c2b7'   # admin charts: one series, hairline grid

def dpi_aware():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

class Fonts:
    """Point sizes matching the original pages' pixel sizes."""
    def __init__(self, root):
        from tkinter import font
        names = set(font.families(root))
        self.family = next((f for f in ('Segoe UI', 'Helvetica Neue', 'Helvetica', 'DejaVu Sans') if f in names), 'TkDefaultFont')

    def __call__(self, size, bold=False):
        return (self.family, size, 'bold' if bold else 'normal')

def setup(root, title, geometry='1440x860'):
    dpi_aware()
    root.title(title)
    root.geometry(geometry)
    root.configure(bg=PAGE)
    f = Fonts(root)
    root.option_add('*Font', f(10))
    s = ttk.Style(root)
    s.theme_use('clam')
    s.configure('TCombobox', padding=(6, 4), foreground='#334f6b', bordercolor='#bacada', arrowsize=12, background=SURFACE)
    s.map('TCombobox', fieldbackground=[('readonly', SURFACE), ('disabled', TOOLBAR)], foreground=[('disabled', '#8a9bb0')],
          selectbackground=[('readonly', SURFACE)], selectforeground=[('readonly', '#334f6b')])
    s.configure('Vertical.TScrollbar', background=TOOLBAR, troughcolor=PAGE, bordercolor=PAGE, arrowcolor=MUTED)
    return f

def header(root, f, title, pill):
    """The dark Care Queue bar: '+' mark, title, and a pill on the right (returned, for status text)."""
    bar = tk.Frame(root, bg=HEAD)
    bar.pack(fill='x')
    inner = tk.Frame(bar, bg=HEAD, height=55)
    inner.pack(fill='x', padx=28)
    inner.pack_propagate(False)
    mark = tk.Label(inner, text='+', bg='#3a5878', fg='white', font=f(15, True), padx=7,
                    highlightthickness=1, highlightbackground='#6c86a1')
    mark.pack(side='left', pady=12)
    tk.Label(inner, text=title, bg=HEAD, fg='white', font=f(13, True)).pack(side='left', padx=10)
    status = tk.Label(inner, text=pill, bg='#33557a', fg=HEAD_INK, font=f(9), padx=10, pady=4,
                      highlightthickness=1, highlightbackground='#55718f')
    status.pack(side='right')
    tk.Frame(root, bg=HEAD_EDGE, height=3).pack(fill='x')
    return status

class Button(tk.Frame):
    """The original's two button styles: 'call' (solid blue) and 'dispatch' (light blue, outlined).
    A 1 px frame draws the outline, which tk.Button does not draw on every platform."""
    def __init__(self, parent, text, command, f, kind='call'):
        bg, edge, ink = (CALL_BG, CALL_EDGE, 'white') if kind == 'call' else (DISPATCH_BG, DISPATCH_EDGE, DISPATCH_INK)
        super().__init__(parent, bg=edge, padx=1, pady=1)
        self.button = tk.Button(self, text=text, command=command, font=f(9, True), bg=bg, fg=ink,
                                activebackground=edge if kind == 'call' else '#dae8f5', activeforeground=ink,
                                disabledforeground='#9fb3c6' if kind == 'call' else '#a7b8c9', relief='flat', bd=0,
                                highlightthickness=0, padx=12, pady=5, cursor='hand2')
        self.button.pack()

    def configure(self, cnf=None, **kw):
        inner = {k: kw.pop(k) for k in ('state', 'text', 'command') if k in kw}
        if inner:
            self.button.configure(**inner)
        return super().configure(cnf, **kw) if cnf or kw else None
    config = configure

def button(parent, text, command, f, kind='call'):
    return Button(parent, text, command, f, kind)

def pill(parent, text, f, bg=FLAG_BG, fg=FLAG_INK, size=8):
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=f(size), padx=6, pady=2)

def connect(root, app_name):
    """A Client for BELLO_SERVER, asking for the token if BELLO_API_TOKEN is not set. None if the user gives up."""
    server = os.environ.get('BELLO_SERVER', 'http://127.0.0.1:8766')
    token = os.environ.get('BELLO_API_TOKEN', '')
    message = f'Access token for the Bello server at {server}:'
    while True:
        if not token:
            token = simpledialog.askstring(f'{app_name} sign-in', message, show='*', parent=root)
            if not token:
                return None
        client = Client(server, token.strip())
        try:
            client.get('/api/languages')
            return client
        except Unauthorized:
            token, message = '', 'That token was not accepted. Access token:'
        except ApiError as error:
            if not messagebox.askretrycancel(app_name, str(error), parent=root):
                return None

def open_app(client, script):
    """Start another staff app with the same server and token (the token is passed only to that process)."""
    env = dict(os.environ, BELLO_SERVER=client.server, BELLO_API_TOKEN=client.token)
    subprocess.Popen([sys.executable, str(HERE / script)], env=env)

class Background:
    """Run slow calls off the UI thread; their results come back on it."""
    def __init__(self, root):
        self.root = root
        self.results = queue.Queue()
        self.pump()

    def run(self, work, done=None, failed=None):
        def go():
            try:
                result = work()
            except Exception as error:
                self.results.put((failed, (str(error),)))
            else:
                self.results.put((done, (result,)))
        threading.Thread(target=go, daemon=True).start()

    def pump(self):
        try:
            while True:
                fn, args = self.results.get_nowait()
                if fn:
                    fn(*args)
        except queue.Empty:
            pass
        try:
            self.root.after(80, self.pump)
        except tk.TclError:
            pass  # window closed

class Tooltip:
    """One floating label moved to the pointer, shared by everything in a window."""
    def __init__(self, root, f):
        self.window = tk.Toplevel(root)
        self.window.overrideredirect(True)
        self.window.withdraw()
        self.label = tk.Label(self.window, bg=INK, fg='white', font=f(9), padx=8, pady=4)
        self.label.pack()

    def show(self, event, text):
        self.label.configure(text=text)
        self.window.geometry(f'+{event.x_root + 14}+{event.y_root - 30}')
        self.window.deiconify()
        self.window.lift()

    def hide(self, _=None):
        self.window.withdraw()

class ScrollFrame(tk.Frame):
    """A vertically scrolling area; put content in `.inner`. The wheel scrolls it while the pointer is over it."""
    def __init__(self, parent, bg=PAGE, **kw):
        super().__init__(parent, bg=bg, **kw)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.window = self.canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.bind_all('<MouseWheel>', self.wheel, add='+')
        self.bind_all('<Button-4>', self.wheel, add='+')
        self.bind_all('<Button-5>', self.wheel, add='+')

    def wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self:
            widget = widget.master
        if widget is None:
            return
        step = -1 if getattr(event, 'num', 0) == 4 or getattr(event, 'delta', 0) > 0 else 1
        self.canvas.yview_scroll(step * 3, 'units')

def clock(seconds):
    return time.strftime('%I:%M %p', time.localtime(seconds)).lstrip('0') if seconds else '—'

def minutes_between(start, end=None):
    return max(0, round(((end or time.time()) - start) / 60))

def language_names(client):
    try:
        return {code: name for code, name, _ in client.get('/api/languages')['languages']}
    except ApiError:
        return {'en': 'English'}
