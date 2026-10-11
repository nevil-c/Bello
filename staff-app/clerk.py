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
COLUMNS = [('request', 'PATIENT REQUEST', None, 300), ('department', 'RECOMMENDED DEPARTMENT', 'department', 180),
           ('urgency', 'URGENCY', 'urgency', 110), ('room', 'ROOM', 'room', 70), ('call', 'ROOM CALL', None, 140),
           ('delivery', 'TRANSCRIPT DELIVERY', None, 230), ('time', 'TIME', 'time', 110)]
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
        lines.append('Sent to ' + r['delivered_department'])
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

class Row:
    """One worklist row: a set of cells on the shared table grid, updated in place."""
    def __init__(self, app, r):
        self.app, self.id, self.r = app, r['id'], r
        t, f = app.table, app.f
        self.edge = tk.Frame(t, width=3)
        self.cells = [tk.Frame(t, padx=14, pady=14) for _ in COLUMNS]
        self.line = tk.Frame(t, height=1, bg=ui.ROW_LINE)
        request, department, urgency, room, call, delivery, when = self.cells
        self.text = tk.Label(request, font=f(10), fg=ui.LINK, wraplength=WRAP, justify='left', anchor='w', cursor='hand2')
        self.text.bind('<Button-1>', lambda e: app.open_detail(self.id))
        self.text.pack(anchor='w', fill='x')
        self.original = tk.Label(request, font=f(9), fg=ui.MUTED, wraplength=WRAP, justify='left', anchor='w')
        self.flags = [ui.pill(request, '', f), ui.pill(request, '', f)]
        # Wrap to the width the column actually gets, which shrinks with the window.
        request.bind('<Configure>', lambda e: [w.configure(wraplength=max(120, e.width - 30)) for w in [self.text, self.original] + self.flags])
        self.department = tk.Label(department, font=f(10), fg=ui.INK_2, wraplength=175, justify='left', anchor='w')
        self.department.pack(anchor='w')
        self.category = tk.Label(department, font=f(8), fg=ui.MUTED, wraplength=175, justify='left', anchor='w')
        self.category.pack(anchor='w')
        self.badge = tk.Label(urgency, font=f(8, True), padx=8, pady=3)
        self.badge.pack(anchor='w')
        self.confidence = tk.Label(urgency, font=f(8), fg=ui.MUTED)
        self.confidence.pack(anchor='w', pady=(3, 0))
        self.room = tk.Label(room, font=f(10, True), fg=ui.INK)
        self.room.pack(anchor='w')
        ui.button(call, 'Speak with patient', lambda: app.open_detail(self.id, call=True), f, 'call').pack(anchor='w')
        self.team = ttk.Combobox(delivery, state='readonly', width=29, font=f(9))
        self.team.bind('<<ComboboxSelected>>', lambda e: self.selected())
        self.send = ui.button(delivery, 'Send + call', lambda: app.dispatch(self.id, self.send), f, 'dispatch')
        self.sent = tk.Label(delivery, font=f(9), fg=ui.INK_2, justify='left', anchor='w')
        self.when = tk.Label(when, font=f(9), fg='#5d7086', justify='left', anchor='w')
        self.when.pack(anchor='w')
        self.recommended = object()

    def selected(self):
        """The clerk changed the team: remember it and say what the button will now do."""
        team = ui.TEAMS[self.team.current()]
        self.app.chosen[self.id] = team
        self.send.configure(text=ui.call_action(team))

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
            self.team.configure(values=[t + (' (recommended)' if t == r['recommended'] else '') for t in ui.TEAMS])
        team = self.app.chosen.get(self.id) or r['delivered_department'] or r['recommended'] or ui.TEAMS[0]
        self.team.current(ui.TEAMS.index(team))
        self.send.configure(text=ui.call_action(team))
        for widget in (self.team, self.send, self.sent):
            widget.pack_forget()
        if sendable(r):
            self.team.pack(anchor='w', pady=(0, 7))
            self.send.configure(state='normal')
            self.send.pack(anchor='w')
        status = delivery_text(r)
        if status:
            self.sent.configure(text=status)
            self.sent.pack(anchor='w', pady=(4, 0))
        self.when.configure(text=time_text(r))

    def destroy(self):
        for widget in [self.edge, self.line] + self.cells:
            widget.destroy()

class Detail(tk.Toplevel):
    """The patient dialog: request, routing, room audio, basic information, delivery and history."""
    def __init__(self, app, rid, call=False):
        super().__init__(app.root)
        self.app, self.id, f = app, rid, app.f
        self.title('Patient request')
        self.configure(bg=ui.SURFACE)
        self.geometry('640x780')
        self.transient(app.root)
        top = tk.Frame(self, bg=ui.DIALOG_HEAD, padx=24, pady=18)
        top.pack(fill='x')
        tk.Frame(self, bg='#cbd7e2', height=1).pack(fill='x')
        heading = tk.Frame(top, bg=ui.DIALOG_HEAD)
        heading.pack(side='left')
        tk.Label(heading, text='PATIENT CONTEXT', font=f(8, True), fg=ui.EYEBROW, bg=ui.DIALOG_HEAD).pack(anchor='w')
        self.heading = tk.Label(heading, font=f(15, True), fg=ui.INK, bg=ui.DIALOG_HEAD)
        self.heading.pack(anchor='w', pady=(4, 0))
        tk.Button(top, text='✕', command=self.destroy, font=f(12), bg=ui.DIALOG_HEAD, relief='flat', bd=0, cursor='hand2').pack(side='right')
        body = ui.ScrollFrame(self, bg=ui.SURFACE)
        body.pack(fill='both', expand=True)
        c = tk.Frame(body.inner, bg=ui.SURFACE, padx=24, pady=16)
        c.pack(fill='both', expand=True)
        ui.pill(c, 'Patient information', f, bg='#edf2f7', fg='#60748a').pack(anchor='w')
        self.h3(c, 'Patient request')
        self.request = self.text(c, 11)
        self.original = self.text(c, 10, fg=ui.MUTED)
        self.routing = self.text(c, 9, fg='#667d95')

        box = tk.Frame(c, bg='#eef4fa', highlightthickness=1, highlightbackground='#c6d6e7', padx=16, pady=14)
        box.pack(fill='x', pady=(20, 0))
        line = tk.Frame(box, bg='#eef4fa')
        line.pack(fill='x')
        left = tk.Frame(line, bg='#eef4fa')
        left.pack(side='left')
        tk.Label(left, text='Room audio', font=f(10, True), fg='#304f70', bg='#eef4fa').pack(anchor='w')
        self.call_room = tk.Label(left, font=f(9), fg=ui.MUTED, bg='#eef4fa')
        self.call_room.pack(anchor='w')
        ui.button(line, 'Speak with patient', self.call_preview, f, 'call').pack(side='right')
        self.preview = tk.Frame(box, bg='#eef4fa')
        self.preview_text = tk.Label(self.preview, font=f(10), fg=ui.INK_2, bg='#eef4fa', wraplength=520, justify='left')
        self.preview_text.pack(anchor='w', pady=(10, 6))
        tk.Button(self.preview, text='Close call preview', command=self.preview.pack_forget, font=f(9), bg='white', fg='#355675',
                  relief='flat', highlightthickness=1, highlightbackground='#bbcbdc', padx=10, pady=4).pack(anchor='w')
        self.disclaimer = tk.Label(box, text='Demo only · Room audio service is not connected.', font=f(8), fg='#657b92', bg='#eef4fa')
        self.disclaimer.pack(anchor='w', pady=(8, 0))

        self.h3(c, 'Basic information')
        grid = tk.Frame(c, bg=ui.SURFACE)
        grid.pack(fill='x')
        self.facts = {}
        for i, name in enumerate(('Spoken language', 'Screen language', 'Category', 'Urgency', 'Review note')):
            tk.Label(grid, text=name, font=f(10), fg='#667b92', bg=ui.SURFACE).grid(row=i, column=0, sticky='nw', pady=4)
            self.facts[name] = tk.Label(grid, font=f(10), fg=ui.INK, bg=ui.SURFACE, wraplength=400, justify='left')
            self.facts[name].grid(row=i, column=1, sticky='nw', padx=(24, 0), pady=4)

        self.h3(c, 'Transcript delivery')
        tk.Label(c, text='Send the request to a shared department inbox and call the department. Any available team member can respond.',
                 font=f(9), fg='#667d95', bg=ui.SURFACE, wraplength=580, justify='left').pack(anchor='w')
        actions = tk.Frame(c, bg=ui.SURFACE)
        actions.pack(anchor='w', pady=(10, 0))
        self.team = ttk.Combobox(actions, state='readonly', values=ui.TEAMS, width=34, font=f(10))
        self.team.bind('<<ComboboxSelected>>', lambda e: self.team_chosen())
        self.team.pack(side='left')
        self.send = ui.button(actions, 'Send + call', lambda: app.dispatch(self.id, self.send, self.message), f, 'call')
        self.send.pack(side='left', padx=10)
        self.message = self.text(c, 9, fg=ui.INK_2)
        self.note = tk.Label(c, font=f(9), fg='#6d7f92', bg=ui.SURFACE, wraplength=580, justify='left')
        self.note.pack(anchor='w', pady=(14, 0))

        self.h3(c, 'Department routing history')
        self.history = self.text(c, 9, fg='#667d95')
        self.shown = None
        self.update_request(app.find(rid))
        if call:
            self.call_preview()

    def team_chosen(self):
        """The clerk picked a team: what the button does follows the team."""
        self.app.chosen[self.id] = self.team.get()
        self.send.configure(text=ui.call_action(self.team.get()))

    def h3(self, parent, text):
        tk.Label(parent, text=text, font=self.app.f(10, True), fg='#304f70', bg=ui.SURFACE).pack(anchor='w', pady=(20, 6))

    def text(self, parent, size, fg=ui.INK_2):
        label = tk.Label(parent, font=self.app.f(size), fg=fg, bg=ui.SURFACE, wraplength=580, justify='left', anchor='w')
        label.pack(anchor='w', fill='x')
        return label

    def call_preview(self):
        r = self.app.find(self.id)
        self.preview_text.configure(text=f"Call preview for room {r['room']}. No audio connection has been made. "
                                         'Connect the room calling service to enable speaking with this patient.')
        self.preview.pack(fill='x', before=self.disclaimer)

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
        urgency = f"Urgency {r['urgency']}/10" if r['urgency'] is not None else 'Urgency not assessed'
        self.routing.configure(text=f"{r['recommended'] or 'No suggestion'} · {urgency}")
        label, _ = level(r['urgency'])
        self.facts['Spoken language'].configure(text=names.get(r['language'], r['language'] or 'Not detected'))
        self.facts['Screen language'].configure(text=names.get(r['ui_language'], r['ui_language'] or 'Not provided'))
        self.facts['Category'].configure(text=r['category'] or 'Not classified')
        self.facts['Urgency'].configure(text=f"{r['urgency']}/10 ({label}" + (f", {round(r['urgency_confidence'] * 100)}% confidence)" if r['urgency_confidence'] is not None else ')') if r['urgency'] is not None else 'Not assessed')
        self.facts['Review note'].configure(text=r['review'] or 'None')
        team = self.app.chosen.get(self.id) or r['delivered_department'] or r['recommended'] or ui.TEAMS[0]
        self.team.set(team)
        ready = sendable(r)
        self.team.configure(state='readonly' if ready else 'disabled')
        self.send.configure(state='normal' if ready else 'disabled')
        status = delivery_text(r)
        self.message.configure(text=status)
        self.note.configure(text='Needs clerk review: ' + r['review'] if r['review'] else 'Review the request and selected team before sending.')
        self.app.bg.run(lambda: self.app.client.get(f'/api/requests/{self.id}/history')['history'], self.show_history)

    def show_history(self, history):
        if self.winfo_exists():
            self.history.configure(text='\n'.join(f"{time.strftime('%d %b %Y, %H:%M', time.localtime(e['created']))} · {e['event']}" for e in history) or 'No history yet.')

class Clerk:
    def __init__(self, root, client, live=True):
        self.root, self.client = root, client
        self.f = f = ui.setup(root, 'Patient requests · Care Queue', '1560x900')
        root.minsize(1400, 640)
        self.status = ui.header(root, f, 'Care Queue', 'Connecting…')
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
        tk.Label(titles, text='UNIT CLERK DASHBOARD', font=f(8, True), fg=ui.EYEBROW, bg=ui.PAGE).pack(anchor='w')
        tk.Label(titles, text='Patient requests', font=f(20), fg=ui.INK, bg=ui.PAGE).pack(anchor='w', pady=(4, 2))
        tk.Label(titles, text='Inpatient call bell · Request worklist', font=f(10), fg=ui.MUTED, bg=ui.PAGE).pack(anchor='w')
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
        entry = tk.Entry(toolbar, textvariable=self.search, width=40, font=f(10), relief='flat', highlightthickness=1,
                         highlightbackground='#b9c9d9', highlightcolor='#356b9c', fg=ui.INK)
        entry.pack(side='right', ipady=6)
        tk.Label(toolbar, text='Search request, room or department', font=f(9), fg=ui.MUTED, bg=ui.TOOLBAR).pack(side='right', padx=8)
        self.show_resolved = tk.BooleanVar(value=False)
        tk.Checkbutton(toolbar, text='Show resolved', variable=self.show_resolved, command=self.render, font=f(9), fg=ui.INK_2,
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
            cell = tk.Frame(self.table, bg=ui.TH_BG, padx=14, pady=11)
            cell.grid(row=0, column=column, sticky='nsew')
            label = tk.Label(cell, text=heading, font=f(8, True), fg=ui.TH_INK, bg=ui.TH_BG)
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

    def open_detail(self, rid, call=False):
        if self.detail is not None and self.detail.winfo_exists():
            self.detail.destroy()
        self.detail = Detail(self, rid, call)

    def dispatch(self, rid, button, message=None):
        r = self.find(rid)
        department = self.chosen.get(rid) or r['recommended'] or ui.TEAMS[0]
        button.configure(state='disabled')
        def done(result):
            self.chosen.pop(rid, None)
            if message is not None and message.winfo_exists():
                phone = f"Call placed to ext {result['phone']} (placeholder)." if result.get('phone') else 'Call logged (placeholder).'
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
