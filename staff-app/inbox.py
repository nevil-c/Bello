"""Bello shared department inbox: a Python/Tkinter version of the original department page.

Requests the clerk has sent arrive here in English; any available team member can acknowledge one.

  BELLO_SERVER=http://<server>:8766 python inbox.py
"""
import time
import tkinter as tk
from tkinter import ttk
import common as ui

REFRESH_MS = 4000

class Inbox:
    def __init__(self, root, client, refresh=True):
        self.root, self.client = root, client
        self.f = f = ui.setup(root, 'Bello department inbox', '1100x820')
        self.bg = ui.Background(root)
        self.names = ui.language_names(client) if client else {'en': 'English'}
        bar = tk.Frame(root, bg=ui.HEAD, padx=28, pady=16)
        bar.pack(fill='x')
        tk.Label(bar, text='Bello · Shared department inbox', font=f(11), fg='white', bg=ui.HEAD).pack(anchor='w')

        main = tk.Frame(root, bg=ui.PAGE)
        main.pack(fill='both', expand=True, padx=40, pady=(24, 16))
        tk.Label(main, text='Patient transcripts', font=f(20, True), fg=ui.INK, bg=ui.PAGE).pack(anchor='w')
        tk.Label(main, text='Shared inbox for each department. Requests are shown in English; any available team member can acknowledge one.',
                 font=f(8), fg='#61758b', bg=ui.PAGE).pack(anchor='w', pady=(6, 12))
        controls = tk.Frame(main, bg=ui.PAGE)
        controls.pack(anchor='w')
        tk.Label(controls, text='Department ', font=f(10), fg=ui.INK, bg=ui.PAGE).pack(side='left')
        # The original inbox's order, with Primary Nurse added now that clinical requests are delivered too.
        self.team = ttk.Combobox(controls, state='readonly', values=ui.TEAMS[1:] + ui.TEAMS[:1], width=30, font=f(10))
        self.team.current(0)
        self.team.bind('<<ComboboxSelected>>', lambda e: self.load())
        self.team.pack(side='left')
        clerk = tk.Label(controls, text='Unit clerk dashboard', font=f(10) + ('underline',), fg=ui.CALL_BG, bg=ui.PAGE, cursor='hand2')
        clerk.pack(side='left', padx=12)
        clerk.bind('<Button-1>', lambda e: ui.open_app(client, 'clerk.py'))
        self.message = tk.Label(main, font=f(10), fg=ui.INK, bg=ui.PAGE, wraplength=900, justify='left')
        self.message.pack(anchor='w', pady=(12, 0))
        scroll = ui.ScrollFrame(main, bg=ui.PAGE)
        scroll.pack(fill='both', expand=True)
        self.cards = scroll.inner
        self.shown = None
        self.next = None
        if refresh:
            self.load()

    def load(self):
        if self.next:
            self.root.after_cancel(self.next)
        team = self.team.get()
        self.bg.run(lambda: self.client.get('/api/departments/queue', department=team)['deliveries'],
                    lambda deliveries: self.show(team, deliveries), self.error)
        self.next = self.root.after(REFRESH_MS, self.load)

    def error(self, text):
        self.message.configure(text=text)

    def show(self, team, deliveries):
        if team != self.team.get() or (team, deliveries) == self.shown:
            return
        self.shown = (team, deliveries)
        f = self.f
        for card in self.cards.winfo_children():
            card.destroy()
        self.message.configure(text='' if deliveries else 'No requests for this department.')
        for d in deliveries:
            card = tk.Frame(self.cards, bg=ui.SURFACE, padx=20, pady=18, highlightthickness=1, highlightbackground='#bdcbd9')
            card.pack(fill='x', pady=(14, 0), padx=(0, 4))
            tk.Label(card, text='Room ' + d['room'], font=f(10, True), fg=ui.INK, bg=ui.SURFACE).pack(anchor='w')
            tk.Label(card, text=d['transcript'], font=f(10), fg=ui.INK, bg=ui.SURFACE, wraplength=900, justify='left').pack(anchor='w', pady=(10, 10))
            language = self.names.get(d['language'], d['language'] or 'unknown')
            when = time.strftime('%d/%m/%Y, %I:%M:%S %p', time.localtime(d['created']))
            tk.Label(card, text=f"Language: {language} · {d['status']} · {when}", font=f(8), fg='#61758b', bg=ui.SURFACE).pack(anchor='w')
            if d['status'] != 'Acknowledged':
                button = ui.button(card, 'Acknowledge', None, f, 'call')
                button.configure(command=lambda d=d, b=button: self.acknowledge(d, b))
                button.pack(anchor='w', pady=(12, 0))

    def acknowledge(self, delivery, button):
        button.configure(state='disabled')
        def failed(text):
            self.message.configure(text=text)
            if button.winfo_exists():
                button.configure(state='normal')
        self.bg.run(lambda: self.client.post(f"/api/deliveries/{delivery['id']}/acknowledge", {}), lambda _: self.load(), failed)

def main():
    ui.dpi_aware()
    root = tk.Tk()
    root.withdraw()
    client = ui.connect(root, 'Department inbox')
    if client is None:
        root.destroy()
        return
    root.deiconify()
    Inbox(root, client)
    root.mainloop()

if __name__ == '__main__':
    main()
