"""Care Queue: the unit clerk dashboard.

A Python/Tkinter version of the original clerk page: the same header, worklist columns, badges and
patient dialog, now fed live from the Bello server with the routing model's suggestions. The model's
department is only a suggestion: the clerk reviews the row and places the call with one click, and
nursing is never dialled (the clerk is already talking to the nurses), so a nursing row offers Send.

  BELLO_SERVER=http://<server>:8766 python clerk.py
"""
import time
import tkinter as tk
from tkinter import messagebox, ttk
import common as ui
from bello_api import LiveRequests

SHOWN_STATES = {'Queued', 'Transcribing', 'Retry required'}
# (key, heading, sort key, minimum width)
COLUMNS = [('request', 'PATIENT REQUEST', None, 270), ('department', 'SUGGESTED DEPARTMENT', 'department', 200),
           ('urgency', 'URGENCY', 'urgency', 130), ('room', 'ROOM', 'room', 95), ('call', 'TALK TO PATIENT', None, 180),
           ('delivery', 'SEND TO DEPARTMENT', None, 325), ('time', 'WAITING', 'time', 120)]
WRAP = 330   # request text width before it wraps

# ---- what a row says (no Tk here, so it can be tested)

def level(urgency):
    """The original badge bands, except Urgent starts at 7: the routing model cannot output 8-10."""
    if urgency is None:
        return 'Not assessed', 'none'
    if urgency >= 7:
        return 'Urgent', 'urgent'
    if urgency >= 5:
        return 'High', 'high'
    if urgency >= 3:
        return 'Routine', 'routine'
    return 'Low', 'low'

def visible(r):
    return bool(r['transcript']) or r['state'] in SHOWN_STATES

def critical(r):
    return not r['resolved'] and (r['urgency'] or 0) >= 7

def request_text(r):
    if r['transcript_en'] or r['transcript']:
        return r['transcript_en'] or r['transcript']
    return {'Queued': 'Recording received…', 'Transcribing': 'Transcribing…'}.get(r['state'], r['error'] or r['state'])

def translated(r):
    return bool(r['language'] and r['language'] != 'en' and r['transcript_en'] and r['transcript_en'] != r['transcript'])

def sendable(r):
    return bool(r['transcript']) and r['state'] == 'Ready to send' and not r['delivered_department']

def delivery_text(r):
    lines = []
    if r['delivered_department']:
        lines.append('✓ Sent to ' + r['delivered_department'])
    elif r['state'] == 'Classifying':
        lines.append('Routing…')
    if r['called']:
        lines.append('Call placed ' + ui.clock(r['called']))
    if r['resolved']:
        lines.append('Done at bedside ' + ui.clock(r['resolved']))
    return '\n'.join(lines)

def time_text(r, now=None):
    if not r['created']:
        return '—'
    if r['resolved']:
        return f"{ui.clock(r['created'])}\nResolved in {ui.minutes_between(r['created'], r['resolved'])} min"
    return f"{ui.clock(r['created'])}\nWaiting {ui.minutes_between(r['created'], now)} min"

def ordered(rows, key, direction):
    # Unassessed requests sort with the urgent ones: nobody has judged them yet.
    def value(r):
        if key == 'urgency':
            return 7.5 if r['urgency'] is None else r['urgency']
        if key == 'time':
            return r['created'] or 0
        if key == 'department':
            return (r['delivered_department'] or r['recommended'] or '').lower()
        return [int(p) if p.isdigit() else p for p in str(r['room']).replace('·', ' ').split()]
    rows = sorted(rows, key=lambda r: (r['created'] or 0, r['id']))
    return sorted(rows, key=value, reverse=direction < 0)

def matches(r, query):
    text = ' '.join(str(r[k] or '') for k in ('transcript', 'transcript_en', 'room', 'recommended', 'delivered_department', 'category'))
    return query.lower() in text.lower()

# ---- the window

class ActionButton(tk.Frame):
    """Consistent, high-contrast actions, including on macOS native Tk themes."""
    def __init__(self, parent, text, command, f, primary=False):
        self.command, self.enabled = command, True
        self.fill = '#187347' if primary else '#e7eff8'
        self.ink = '#ffffff' if primary else '#234f79'
        self.hover = '#105b37' if primary else '#d6e5f5'
        super().__init__(parent, bg='#14633d' if primary else '#9bb5ce', padx=1, pady=1)
        self.label = tk.Label(self, text=text, font=f(13, True), bg=self.fill, fg=self.ink,
                              width=17, padx=4, pady=10, cursor='hand2', takefocus=True,
                              highlightthickness=2, highlightbackground=self.fill)
        self.label.pack(fill='both', expand=True)
        self.label.bind('<Button-1>', self.invoke)
        self.label.bind('<Return>', self.invoke)
        self.label.bind('<space>', self.invoke)
        self.label.bind('<Enter>', lambda e: self.label.configure(bg=self.hover if self.enabled else self.fill))
        self.label.bind('<Leave>', lambda e: self.label.configure(bg=self.fill))
        self.label.bind('<FocusIn>', lambda e: self.label.configure(highlightbackground='#234f79'))
        self.label.bind('<FocusOut>', lambda e: self.label.configure(highlightbackground=self.fill))

    def invoke(self, event=None):
        if self.enabled:
            self.command()

    def configure(self, cnf=None, **kw):
        if 'state' in kw:
            self.enabled = kw.pop('state') != 'disabled'
            self.label.configure(fg=self.ink if self.enabled else '#d2e1d9', cursor='hand2' if self.enabled else '')
        if 'text' in kw:
            self.label.configure(text=kw.pop('text'))
        return super().configure(cnf, **kw) if cnf or kw else None


class Row:
    """One worklist row: a set of cells on the shared table grid, updated in place."""
    def __init__(self, app, r):
        self.app, self.id, self.r = app, r['id'], r
        t, f = app.table, app.f
        self.edge = tk.Frame(t, width=3)
        self.cells = [tk.Frame(t, width=width, padx=14, pady=16) for _, _, _, width in COLUMNS]
        self.line = tk.Frame(t, height=1, bg=ui.ROW_LINE)
        request, department, urgency, room, call, delivery, when = self.cells
        self.text = tk.Label(request, font=f(15), fg=ui.LINK, wraplength=WRAP, justify='left', anchor='w', cursor='hand2')
        self.text.bind('<Button-1>', lambda e: app.open_detail(self.id))
        self.text.pack(anchor='w', fill='x')
        self.original = tk.Label(request, font=f(12), fg=ui.MUTED, wraplength=WRAP, justify='left', anchor='w')
        self.flags = [ui.pill(request, '', f), ui.pill(request, '', f)]
        # Wrap to the width the column actually gets, which shrinks with the window.
        request.bind('<Configure>', lambda e: [w.configure(wraplength=max(120, e.width - 30)) for w in [self.text, self.original] + self.flags])
        self.department = tk.Label(department, font=f(13), fg=ui.INK_2, wraplength=180, justify='left', anchor='w')
        self.department.pack(anchor='w')
        self.category = tk.Label(department, font=f(8), fg=ui.MUTED, wraplength=175, justify='left', anchor='w')
        # Category is available in the patient details.
        self.badge = tk.Label(urgency, font=f(12, True), padx=8, pady=3)
        self.badge.pack(anchor='w')
        self.confidence = tk.Label(urgency, font=f(8), fg=ui.MUTED)
        # Model confidence is available in the patient details.
        self.room = tk.Label(room, font=f(15, True), fg=ui.INK)
        self.room.pack(anchor='w')
        self.talk = ui.hold_to_speak(call, f)
        self.talk.pack(anchor='w')
        self.team = ttk.Combobox(delivery, state='readonly', width=1, font=f(14))
        self.team.bind('<<ComboboxSelected>>', lambda e: self.selected())
        self.action_slot = tk.Frame(delivery, height=54)
        self.action_slot.pack_propagate(False)
        self.send = ActionButton(self.action_slot, 'Send + call', lambda: app.dispatch(self.id, self.send), f, primary=True)
        self.sent = tk.Label(delivery, font=f(12), wraplength=250, fg='#187347', justify='left', anchor='w')
        self.when = tk.Label(when, font=f(12), fg='#5d7086', justify='left', anchor='w')
        self.when.pack(anchor='w')
        self.recommended = object()

    def selected(self):
        """The clerk changed the team: remember it and say what the button will now do."""
        team = ui.TEAMS[self.team.current()]
        self.app.chosen[self.id] = team
        self.send.configure(text=ui.call_action(team))
        self.send.pack_forget()
        if team not in ui.NO_CALL and sendable(self.r):
            self.send.pack(anchor='w')

    def place(self, index):
        row = 2 * index + 2   # rows 0 and 1 are the heading and its rule
        self.edge.grid(row=row, column=0, sticky='ns')
        for column, cell in enumerate(self.cells, start=1):
            cell.grid(row=row, column=column, sticky='nsew')
        self.line.grid(row=row + 1, column=0, columnspan=len(COLUMNS) + 1, sticky='ew')

    def update(self, r, stripe):
        self.r = r
        bg = ui.CRITICAL_BG if critical(r) else ui.STRIPE if stripe else ui.SURFACE
        self.edge.configure(bg=ui.CRITICAL_EDGE if critical(r) else bg)
        for cell in self.cells:
            cell.configure(bg=bg)
            for child in cell.winfo_children():
                if isinstance(child, tk.Label) and child not in self.flags and child is not self.badge:
                    child.configure(bg=bg)
        self.text.configure(text=request_text(r), fg=ui.LINK if r['transcript'] else ui.MUTED)
        name = self.app.names.get(r['language'], r['language'])
        self.original.configure(text=f"{name} (original): {r['transcript']}")
        notes = (['Machine translation: check the original'] if translated(r) else []) + ([r['review']] if r['review'] else [])
        self.original.pack_forget()
        for flag in self.flags:
            flag.pack_forget()
        if translated(r):
            self.original.pack(anchor='w', fill='x', pady=(4, 0))
        for flag, note in zip(self.flags, notes):
            flag.configure(text=note)
            flag.pack(anchor='w', pady=(6, 0))
        self.department.configure(text=r['recommended'] or ('Routing…' if r['state'] == 'Classifying' else 'No suggestion'))
        self.category.configure(text=r['category'] or '')
        label, kind = level(r['urgency'])
        background, ink = ui.BADGES[kind]
        self.badge.configure(text=f"●  {label} · {r['urgency']}" if r['urgency'] is not None else f'●  {label}', bg=background, fg=ink)
        self.confidence.configure(text=f"{round(r['urgency_confidence'] * 100)}% confidence" if r['urgency_confidence'] is not None else '')
        self.room.configure(text=r['room'])
        if r['recommended'] != self.recommended:
            self.recommended = r['recommended']
            self.team.configure(values=ui.TEAMS)
        team = self.app.chosen.get(self.id) or r['delivered_department'] or r['recommended'] or ui.TEAMS[0]
        self.team.current(ui.TEAMS.index(team))
        self.send.configure(text=ui.call_action(team))
        self.action_slot.configure(bg=bg)
        for widget in (self.team, self.action_slot, self.send, self.sent):
            widget.pack_forget()
        if sendable(r):
            self.team.pack(fill='x', pady=(0, 10))
            self.send.configure(state='normal')
            self.action_slot.pack(fill='x')
            if team not in ui.NO_CALL:
                self.send.pack(anchor='w')
        status = delivery_text(r)
        if status:
            self.sent.configure(text=status)
            self.sent.pack(anchor='w', pady=(4, 0))
        self.when.configure(text=time_text(r))

    def destroy(self):
        for widget in [self.edge, self.line] + self.cells:
            widget.destroy()

class PatientScrollFrame(tk.Frame):
    """Pixel scrolling with bounded travel and window-local wheel handlers."""
    def __init__(self, parent):
        super().__init__(parent, bg=ui.SURFACE)
        self.canvas = tk.Canvas(self, bg=ui.SURFACE, highlightthickness=0, yscrollincrement=1)
        bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        bar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.inner = tk.Frame(self.canvas, bg=ui.SURFACE)
        self.window = self.canvas.create_window(0, 0, window=self.inner, anchor='nw')
        self.canvas.configure(yscrollcommand=bar.set)
        self.inner.bind('<Configure>', self.update_region)
        self.canvas.bind('<Configure>', self.resize)
        self.host = self.winfo_toplevel()
        self.bindings = [(sequence, self.host.bind(sequence, self.wheel, add='+'))
                         for sequence in ('<MouseWheel>', '<Button-4>', '<Button-5>')]
        self.bind('<Destroy>', self.cleanup)

    def resize(self, event):
        self.canvas.itemconfigure(self.window, width=event.width)
        self.update_region()

    def update_region(self, event=None):
        height = max(self.inner.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))

    def wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        while widget is not None and widget is not self:
            widget = widget.master
        if widget is None:
            return
        number, delta = getattr(event, 'num', None), getattr(event, 'delta', 0)
        if number in (4, 5):
            pixels = -32 if number == 4 else 32
        elif not delta:
            return
        elif self.tk.call('tk', 'windowingsystem') == 'aqua':
            pixels = max(-40, min(40, -delta * 2))
        else:
            pixels = -delta / 120 * 32
        height = max(self.inner.winfo_reqheight(), self.canvas.winfo_height())
        maximum = max(0, height - self.canvas.winfo_height())
        offset = min(maximum, max(0, self.canvas.canvasy(0) + pixels))
        self.canvas.yview_moveto(offset / height if height else 0)
        return 'break'

    def cleanup(self, event):
        if event.widget is self:
            for sequence, binding in self.bindings:
                self.host.unbind(sequence, binding)


class Detail(tk.Toplevel):
    """The patient dialog: request, routing, room audio, basic information, delivery and history."""
    def __init__(self, app, rid):
        super().__init__(app.root)
        self.app, self.id, f = app, rid, app.f
        self.holding = False
        self.title('Patient request')
        self.configure(bg=ui.SURFACE)
        self.geometry('820x880')
        self.minsize(700, 640)
        self.transient(app.root)
        top = tk.Frame(self, bg=ui.DIALOG_HEAD, padx=24, pady=18)
        top.pack(fill='x')
        tk.Frame(self, bg='#cbd7e2', height=1).pack(fill='x')
        heading = tk.Frame(top, bg=ui.DIALOG_HEAD)
        heading.pack(side='left')
        tk.Label(heading, text='PATIENT REQUEST', font=f(11, True), fg=ui.EYEBROW, bg=ui.DIALOG_HEAD).pack(anchor='w')
        self.heading = tk.Label(heading, font=f(20, True), fg=ui.INK, bg=ui.DIALOG_HEAD)
        self.heading.pack(anchor='w', pady=(4, 0))
        ActionButton(top, 'Close', self.destroy, f).pack(side='right')
        body = PatientScrollFrame(self)
        body.pack(fill='both', expand=True)
        c = tk.Frame(body.inner, bg=ui.SURFACE, padx=24, pady=16)
        c.pack(fill='both', expand=True)

        self.h3(c, 'What the patient needs')
        self.request = self.text(c, 18)
        self.original = self.text(c, 13, fg=ui.MUTED)
        self.routing = self.text(c, 14, fg=ui.INK)

        box = tk.Frame(c, bg='#eef4fa', highlightthickness=1, highlightbackground='#c6d6e7', padx=16, pady=14)
        box.pack(fill='x', pady=(20, 0))
        line = tk.Frame(box, bg='#eef4fa')
        line.pack(fill='x')
        left = tk.Frame(line, bg='#eef4fa')
        left.pack(side='left')
        tk.Label(left, text='Talk to the patient', font=f(14, True), fg='#304f70', bg='#eef4fa').pack(anchor='w')
        self.call_room = tk.Label(left, font=f(13), fg=ui.MUTED, bg='#eef4fa')
        self.call_room.pack(anchor='w')
        self.talk = ui.hold_to_speak(line, f, self.call_state)
        self.talk.pack(side='right')
        self.preview = tk.Frame(box, bg='#eef4fa')
        self.preview_text = tk.Label(self.preview, font=f(13), fg=ui.INK_2, bg='#eef4fa', wraplength=520, justify='left')
        self.preview_text.pack(anchor='w', pady=(10, 6))
        ActionButton(self.preview, 'Close preview', self.preview.pack_forget, f).pack(anchor='w')

        self.h3(c, 'Send to department')
        tk.Label(c, text='Confirm the department, then send the request. Nursing requests are sent without a phone call.',
                 font=f(13), fg=ui.INK_2, bg=ui.SURFACE, wraplength=740, justify='left').pack(anchor='w')
        actions = tk.Frame(c, bg=ui.SURFACE)
        actions.pack(fill='x', pady=(12, 0))
        self.team = ttk.Combobox(actions, state='readonly', values=ui.TEAMS, width=1, font=f(14))
        self.team.bind('<<ComboboxSelected>>', lambda e: self.team_chosen())
        self.team.pack(side='left', fill='x', expand=True, ipady=5)
        self.action_slot = tk.Frame(actions, width=190, height=56, bg=ui.SURFACE)
        self.action_slot.pack(side='right', padx=(10, 0))
        self.action_slot.pack_propagate(False)
        self.send = ActionButton(self.action_slot, 'Send + call', lambda: app.dispatch(self.id, self.send, self.message), f, primary=True)
        self.send.pack(anchor='e')
        self.message = self.text(c, 13, fg='#187347')
        self.note = tk.Label(c, font=f(12), fg=ui.MUTED, bg=ui.SURFACE, wraplength=740, justify='left')
        self.note.pack(anchor='w', pady=(14, 0))

        self.h3(c, 'Request details')
        grid = tk.Frame(c, bg=ui.SURFACE)
        grid.pack(fill='x')
        grid.columnconfigure(1, weight=1)
        self.facts = {}
        for i, name in enumerate(('Spoken language', 'Screen language', 'Category', 'Urgency', 'Review note')):
            tk.Label(grid, text=name, font=f(13), fg='#667b92', bg=ui.SURFACE).grid(row=i, column=0, sticky='nw', pady=7)
            self.facts[name] = tk.Label(grid, font=f(13), fg=ui.INK, bg=ui.SURFACE, wraplength=400, justify='left')
            self.facts[name].grid(row=i, column=1, sticky='nw', padx=(24, 0), pady=7)

        self.h3(c, 'Request history')
        self.history = self.text(c, 12, fg=ui.INK_2)
        c.bind('<Configure>', lambda e: self.resize_text(c, max(180, e.width - 48)))
        self.shown = None
        self.update_request(app.find(rid))
        self.call_preview()

    def resize_text(self, parent, width):
        if getattr(self, '_text_width', None) == width:
            return
        self._text_width = width
        for widget in parent.winfo_children():
            if isinstance(widget, tk.Label):
                widget.configure(wraplength=width)

    def team_chosen(self):
        """The clerk picked a team: what the button does follows the team."""
        self.app.chosen[self.id] = self.team.get()
        self.send.configure(text=ui.call_action(self.team.get()))
        self.send.pack_forget()
        if self.team.get() not in ui.NO_CALL and sendable(self.app.find(self.id)):
            self.send.pack(anchor='e')

    def h3(self, parent, text):
        tk.Label(parent, text=text, font=self.app.f(14, True), fg='#304f70', bg=ui.SURFACE).pack(anchor='w', pady=(24, 10))

    def text(self, parent, size, fg=ui.INK_2):
        label = tk.Label(parent, font=self.app.f(size), fg=fg, bg=ui.SURFACE, wraplength=740, justify='left', anchor='w')
        label.pack(anchor='w', fill='x')
        return label

    def call_preview(self):
        """What the room-audio box says before the clerk holds the talk button down."""
        self.preview_text.configure(text=f"Room {self.app.find(self.id)['room']} · hold the button to talk to the patient.")
        self.preview.pack(fill='x')

    def call_state(self, live):
        """The talk button went down or up: the box reads as a live call and is timed.

        ponytail: the timer is all there is — nothing is recorded or played. Run the room calling
        service from here when there is one."""
        self.holding = live
        if live:
            self.held = time.time()
            self.preview.pack(fill='x')
            self.call_tick()
        else:
            self.preview_text.configure(text=f"Call ended · {int(time.time() - self.held)}s")

    def call_tick(self):
        # On the root window, not the dialog: a pending `after` on a destroyed Toplevel leaves a
        # Tcl command behind, and the root then fails to destroy it.
        if not self.holding or not self.winfo_exists():
            return
        room = (self.app.find(self.id) or {}).get('room', '')
        self.preview_text.configure(text=f"Speaking with room {room} · {int(time.time() - self.held)}s")
        self.app.root.after(1000, self.call_tick)

    def update_request(self, r):
        if r is None or r == self.shown:
            return
        self.shown = r
        names = self.app.names
        self.heading.configure(text=f"Patient · Room {r['room']}")
        self.call_room.configure(text=f"Room {r['room']}")
        self.request.configure(text=request_text(r))
        self.original.configure(text=f"{names.get(r['language'], r['language'])} (original): {r['transcript']}\n"
                                     'Machine translation: it can be wrong, so check the original.' if translated(r) else '')
        urgency = f"{level(r['urgency'])[0]} · {r['urgency']}/10" if r['urgency'] is not None else 'Urgency not assessed'
        self.routing.configure(text=f"{urgency}  •  Suggested: {r['recommended'] or 'No suggestion'}",
                               fg=ui.BADGES[level(r['urgency'])[1]][1])
        label, _ = level(r['urgency'])
        self.facts['Spoken language'].configure(text=names.get(r['language'], r['language'] or 'Not detected'))
        self.facts['Screen language'].configure(text=names.get(r['ui_language'], r['ui_language'] or 'Not provided'))
        self.facts['Category'].configure(text=r['category'] or 'Not classified')
        self.facts['Urgency'].configure(text=f"{r['urgency']}/10 ({label}" + (f", {round(r['urgency_confidence'] * 100)}% confidence)" if r['urgency_confidence'] is not None else ')') if r['urgency'] is not None else 'Not assessed')
        self.facts['Review note'].configure(text=r['review'] or 'No review needed')
        team = self.app.chosen.get(self.id) or r['delivered_department'] or r['recommended'] or ui.TEAMS[0]
        self.team.set(team)
        ready = sendable(r)
        self.team.configure(state='readonly' if ready else 'disabled')
        self.send.configure(text=ui.call_action(team), state='normal' if ready else 'disabled')
        self.send.pack_forget()
        if team not in ui.NO_CALL:
            self.send.pack(anchor='e')
        status = delivery_text(r)
        self.message.configure(text=status)
        self.note.configure(text='Needs clerk review: ' + r['review'] if r['review'] else 'Review the request and selected team before sending.')
        if self.app.client is not None:
            self.app.bg.run(lambda: self.app.client.get(f'/api/requests/{self.id}/history')['history'], self.show_history)
        else:
            self.show_history([])

    def show_history(self, history):
        if self.winfo_exists():
            self.history.configure(text='\n'.join(f"{time.strftime('%d %b %Y, %H:%M', time.localtime(e['created']))} · {e['event']}" for e in history) or 'No history yet.')

class Clerk:
    def __init__(self, root, client, live=True):
        self.root, self.client = root, client
        self.f = f = ui.setup(root, 'Patient requests · Care Queue', '1560x900')
        root.minsize(1500, 640)
        root.option_add('*TCombobox*Listbox.font', f(14))
        self.status = ui.header(root, f, 'Unit Clerk Dashboard', 'Connecting…', centered=True)
        self.bg = ui.Background(root)
        self.names = ui.language_names(client) if client else {'en': 'English'}
        self.requests, self.rows, self.chosen = [], {}, {}
        self.sort, self.direction, self.detail = 'urgency', -1, None

        main = tk.Frame(root, bg=ui.PAGE)
        main.pack(fill='both', expand=True, padx=28, pady=(22, 16))
        top = tk.Frame(main, bg=ui.PAGE)
        top.pack(fill='x', pady=(0, 18))
        titles = tk.Frame(top, bg=ui.PAGE)
        titles.pack(side='left')
        tk.Label(titles, text='Patient requests', font=f(28), fg=ui.INK, bg=ui.PAGE).pack(anchor='w', pady=(4, 2))
        self.count = tk.Label(top, font=f(10, True), fg=ui.COUNT_INK, bg=ui.COUNT_BG, padx=14, pady=8,
                              highlightthickness=1, highlightbackground=ui.COUNT_EDGE)
        self.count.pack(side='right')

        panel = tk.Frame(main, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER)
        panel.pack(fill='both', expand=True)
        toolbar = tk.Frame(panel, bg=ui.TOOLBAR, padx=16, pady=11)
        toolbar.pack(fill='x')
        tk.Label(toolbar, text='Request worklist · ', font=f(10, True), fg=ui.INK_2, bg=ui.TOOLBAR).pack(side='left')
        inbox = tk.Label(toolbar, text='Department inbox', font=f(10, True) + ('underline',), fg='#1a4fb0', bg=ui.TOOLBAR, cursor='hand2')
        inbox.pack(side='left')
        inbox.bind('<Button-1>', lambda e: ui.open_app(client, 'inbox.py'))
        self.search = tk.StringVar()
        self.search.trace_add('write', lambda *a: self.render())
        entry = tk.Entry(toolbar, textvariable=self.search, width=26, font=f(12), relief='flat', highlightthickness=1,
                         highlightbackground='#b9c9d9', highlightcolor='#356b9c', fg=ui.INK)
        entry.pack(side='right', ipady=6)
        tk.Label(toolbar, text='Search', font=f(12), fg=ui.MUTED, bg=ui.TOOLBAR).pack(side='right', padx=8)
        self.show_resolved = tk.BooleanVar(value=False)
        tk.Checkbutton(toolbar, text='Show resolved', variable=self.show_resolved, command=self.render, font=f(12), fg=ui.INK_2,
                       bg=ui.TOOLBAR, activebackground=ui.TOOLBAR, selectcolor='white').pack(side='right', padx=16)
        tk.Frame(panel, bg='#c8d4e0', height=1).pack(fill='x')

        footer = tk.Frame(panel, bg=ui.TOOLBAR, padx=16, pady=11)
        footer.pack(side='bottom', fill='x')
        tk.Frame(panel, bg='#cbd7e2', height=1).pack(side='bottom', fill='x')
        tk.Label(footer, text='Click a column heading to sort', font=f(9), fg='#64788d', bg=ui.TOOLBAR).pack(side='right')

        scroll = ui.ScrollFrame(panel, bg=ui.SURFACE)
        scroll.pack(fill='both', expand=True)
        self.table = tk.Frame(scroll.inner, bg=ui.SURFACE)
        self.table.pack(fill='x')
        self.table.columnconfigure(0, minsize=3)
        self.headings = {}
        for column, (key, heading, sort, width) in enumerate(COLUMNS, start=1):
            self.table.columnconfigure(column, minsize=width, weight=1 if key == 'request' else 0)
            # Prevent changing row content from resizing the shared columns.

            cell = tk.Frame(self.table, bg=ui.TH_BG, padx=14, pady=11)
            cell.grid(row=0, column=column, sticky='nsew')
            label = tk.Label(cell, text=heading, font=f(11, True), fg=ui.TH_INK, bg=ui.TH_BG)
            label.pack(anchor='w')
            if sort:
                label.configure(cursor='hand2')
                label.bind('<Button-1>', lambda e, s=sort: self.sort_by(s))
                self.headings[sort] = (label, heading)
        tk.Frame(self.table, bg=ui.TH_BG).grid(row=0, column=0, sticky='nsew')
        tk.Frame(self.table, bg=ui.COUNT_EDGE, height=1).grid(row=1, column=0, columnspan=len(COLUMNS) + 1, sticky='ew')
        self.empty = tk.Label(scroll.inner, text='No requests match this view.', font=f(10), fg='#718184', bg=ui.SURFACE, pady=50)
        tk.Label(main, text='Select a request to view patient information.', font=f(8), fg='#657a90', bg=ui.PAGE).pack(anchor='w', pady=(12, 0))

        if live:
            self.feed = LiveRequests(client)
            self.feed.start()
            self.drain()
        root.after(30000, self.tick)
        self.render()

    def drain(self):
        while not self.feed.updates.empty():
            kind, value = self.feed.updates.get()
            if kind == 'requests':
                self.requests = value
                self.render()
            else:
                self.status.configure(text=value)
        self.root.after(150, self.drain)

    def tick(self):
        self.render()   # keeps the waiting times current
        self.root.after(30000, self.tick)

    def find(self, rid):
        return next((r for r in self.requests if r['id'] == rid), None)

    def sort_by(self, key):
        self.direction = -self.direction if key == self.sort else (-1 if key == 'urgency' else 1)
        self.sort = key
        self.render()

    def shown(self):
        rows = [r for r in self.requests if visible(r) and (self.show_resolved.get() or not r['resolved']) and matches(r, self.search.get())]
        return ordered(rows, self.sort, self.direction)

    def render(self):
        rows = self.shown()
        ids = {r['id'] for r in rows}
        for rid in [rid for rid in self.rows if rid not in ids]:
            self.rows.pop(rid).destroy()
        for index, r in enumerate(rows):
            row = self.rows.get(r['id'])
            if row is None:
                row = self.rows[r['id']] = Row(self, r)
            row.update(r, index % 2 == 1)
            row.place(index)
        if rows:
            self.empty.pack_forget()
        else:
            self.empty.pack(fill='x')
        open_count = sum(1 for r in self.requests if visible(r) and not r['resolved'])
        self.count.configure(text=f'{open_count} open requests')
        for key, (label, heading) in self.headings.items():
            arrow = ('↓' if self.direction < 0 else '↑') if key == self.sort else '↕'
            label.configure(text=f'{heading}  {arrow}')
        if self.detail is not None and self.detail.winfo_exists():
            self.detail.update_request(self.find(self.detail.id))

    def open_detail(self, rid):
        if self.detail is not None and self.detail.winfo_exists():
            self.detail.destroy()
        self.detail = Detail(self, rid)

    def dispatch(self, rid, button, message=None):
        r = self.find(rid)
        department = self.chosen.get(rid) or r['recommended'] or ui.TEAMS[0]
        button.configure(state='disabled')
        def done(result):
            self.chosen.pop(rid, None)
            if result.get('call_error'):
                messagebox.showwarning('Send + call', f"Sent to {result['department']}, but the call did not go out:\n\n{result['call_error']}", parent=self.root)
            if message is not None and message.winfo_exists():
                if result.get('called'):
                    phone = f"Call placed to {result['phone']}." if result.get('phone') else 'Call placed.'
                else:
                    phone = 'No call placed.'
                message.configure(text=f"Sent to {result['department']}. {phone}")
        def failed(error):
            if button.winfo_exists():
                button.configure(state='normal')
            messagebox.showerror('Send + call', error, parent=self.root)
        self.bg.run(lambda: self.client.post(f'/api/requests/{rid}/dispatch', {'department': department, 'call': True}), done, failed)

def main():
    ui.dpi_aware()
    root = tk.Tk()
    root.withdraw()
    client = ui.connect(root, 'Care Queue')
    if client is None:
        root.destroy()
        return
    root.deiconify()
    Clerk(root, client)
    root.mainloop()

if __name__ == '__main__':
    main()
