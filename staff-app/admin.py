"""Bello admin analytics: the most common patient requests and response times, from the cloud archive.

Response time runs from the patient's call until staff mark the request done at the bedside.
The numbers come from the server (GET /api/admin/stats), which reads the AWS archive.

  BELLO_SERVER=http://<server>:8766 python admin.py
"""
import time
import tkinter as tk
from tkinter import ttk
import common as ui

REFRESH_MS = 60000

def duration(minutes):
    if minutes is None:
        return '—'
    return f'{minutes:g} min' if minutes < 60 else f'{minutes / 60:.1f} h'

def nice_max(value):
    if value <= 0:
        return 1
    import math
    step = 10 ** math.floor(math.log10(value))
    return math.ceil(value / step) * step

def hour_label(h):
    return '12a' if h == 0 else f'{h}a' if h < 12 else '12p' if h == 12 else f'{h - 12}p'

class Chart(tk.Canvas):
    """Redraws itself at its current width; hover shows a tooltip for the mark under the pointer."""
    def __init__(self, parent, app, height):
        super().__init__(parent, bg=ui.SURFACE, highlightthickness=0, height=height)
        self.app, self.data = app, None
        self.bind('<Configure>', lambda e: self.redraw())

    def set(self, *data):
        self.data = data
        self.redraw()

    def hover(self, item, text):
        self.tag_bind(item, '<Motion>', lambda e: self.app.tooltip.show(e, text))
        self.tag_bind(item, '<Leave>', self.app.tooltip.hide)

    def redraw(self):
        self.delete('all')
        if self.data is not None and self.winfo_width() > 50:
            self.draw(self.winfo_width(), *self.data)

class Bars(Chart):
    """Horizontal bars, one series: label on the left, value at the end of the bar."""
    ROW = 30

    def __init__(self, parent, app, label_width=190):
        super().__init__(parent, app, 60)
        self.label_width = label_width

    def draw(self, width, items, value, label, value_text, tip):
        from tkinter import font
        f = self.app.f
        measure = font.Font(font=f(9)).measure
        left = max([self.label_width] + [measure(label(d)) + 18 for d in items])
        self.configure(height=max(1, len(items)) * self.ROW)
        top = nice_max(max([value(d) or 0 for d in items] + [0]))
        plot = max(40, width - left - 80)
        for i, d in enumerate(items):
            y, v = i * self.ROW, value(d) or 0
            w = max(2 if v > 0 else 0, v / top * plot)
            hit = self.create_rectangle(0, y, width, y + self.ROW, fill=ui.SURFACE, outline='')
            name = self.create_text(0, y + self.ROW / 2, text=label(d), anchor='w', font=f(9), fill=ui.INK_2)
            # Bars are capped at 18 px thick so the band's leftover reads as air between them.
            bar = self.create_rectangle(left, y + 6, left + w, y + self.ROW - 6, fill=ui.SERIES, outline='')
            text = self.create_text(left + w + 8, y + self.ROW / 2, text=value_text(d), anchor='w', font=f(9), fill=ui.INK)
            for item in (hit, name, bar, text):
                self.hover(item, tip(d))
        self.create_line(left, 0, left, len(items) * self.ROW, fill=ui.AXIS)

class Columns(Chart):
    """Columns over an ordered axis (hours or days) with hairline gridlines."""
    def __init__(self, parent, app):
        super().__init__(parent, app, 200)

    def draw(self, width, values, labels, every, tip):
        f, height, left, bottom, head = self.app.f, 200, 34, 24, 10
        plot_w, plot_h = width - left, height - bottom - head
        top = nice_max(max(values + [1]))
        for t in (0, top / 2, top):
            y = head + plot_h - t / top * plot_h
            self.create_line(left, y, width, y, fill=ui.GRID if t else ui.AXIS)
            self.create_text(left - 6, y, text=f'{t:g}', anchor='e', font=f(8), fill='#898781')
        band = plot_w / max(1, len(values))
        w = min(22, band - 3)
        for i, v in enumerate(values):
            x = left + i * band
            hit = self.create_rectangle(x, head, x + band, head + plot_h, fill=ui.SURFACE, outline='')
            self.hover(hit, tip(i, v))
            if v:
                bar = self.create_rectangle(x + (band - w) / 2, head + plot_h - v / top * plot_h, x + (band + w) / 2, head + plot_h, fill=ui.SERIES, outline='')
                self.hover(bar, tip(i, v))
            if i % every == 0:
                self.create_text(x + band / 2, height - 8, text=labels[i], font=f(8), fill='#898781')

class Admin:
    def __init__(self, root, client, refresh=True):
        self.root, self.client = root, client
        self.f = f = ui.setup(root, 'Bello Admin Analytics', '1280x900')
        self.status = ui.header(root, f, 'Bello Admin', 'Loading…')
        self.bg = ui.Background(root)
        self.tooltip = ui.Tooltip(root, f)
        self.names = ui.language_names(client) if client else {'en': 'English'}
        self.days, self.next = 7, None

        scroll = ui.ScrollFrame(root, bg=ui.PAGE)
        scroll.pack(fill='both', expand=True)
        main = tk.Frame(scroll.inner, bg=ui.PAGE, padx=28, pady=22)
        main.pack(fill='both', expand=True)
        top = tk.Frame(main, bg=ui.PAGE)
        top.pack(fill='x', pady=(0, 18))
        titles = tk.Frame(top, bg=ui.PAGE)
        titles.pack(side='left')
        tk.Label(titles, text='HOSPITAL ADMINISTRATION', font=f(8, True), fg=ui.EYEBROW, bg=ui.PAGE).pack(anchor='w')
        tk.Label(titles, text='Patient requests and response times', font=f(20), fg=ui.INK, bg=ui.PAGE).pack(anchor='w', pady=(4, 2))
        self.site = tk.Label(titles, text='From the cloud archive.', font=f(10), fg=ui.MUTED, bg=ui.PAGE)
        self.site.pack(anchor='w')
        ranges = tk.Frame(top, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER, padx=3, pady=3)
        ranges.pack(side='right', anchor='s')
        self.range_buttons = {}
        for days, text in ((1, 'Today'), (7, '7 days'), (30, '30 days')):
            b = tk.Button(ranges, text=text, font=f(9), relief='flat', bd=0, padx=12, pady=5, cursor='hand2',
                          command=lambda d=days: self.choose(d))
            b.pack(side='left', padx=1)
            self.range_buttons[days] = b

        self.tiles = tk.Frame(main, bg=ui.PAGE)
        self.tiles.pack(fill='x')
        self.tile_labels = []
        for i in range(5):
            tile = tk.Frame(self.tiles, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER, padx=16, pady=14)
            tile.grid(row=0, column=i, sticky='nsew', padx=(0 if i == 0 else 12, 0))
            self.tiles.columnconfigure(i, weight=1, uniform='tile')
            labels = [tk.Label(tile, font=f(9), fg=ui.INK_2, bg=ui.SURFACE), tk.Label(tile, font=f(20, True), fg=ui.INK, bg=ui.SURFACE),
                      tk.Label(tile, font=f(8), fg=ui.MUTED, bg=ui.SURFACE, wraplength=200, justify='left')]
            for label in labels:
                label.pack(anchor='w')
            self.tile_labels.append(labels)

        self.message = tk.Label(main, font=f(10), fg=ui.INK_2, bg=ui.SURFACE, pady=40, highlightthickness=1, highlightbackground=ui.BORDER)
        self.grid = tk.Frame(main, bg=ui.PAGE)
        self.grid.pack(fill='x', pady=(16, 0))
        self.grid.columnconfigure(0, weight=1, uniform='card')
        self.grid.columnconfigure(1, weight=1, uniform='card')
        self.issues = Bars(self.card(0, 0, 'Most common issues', 'Requests by category from the routing model.'), self)
        self.by_department = Bars(self.card(0, 1, 'Response time by department', 'Median minutes from call to done. Hover for the 90th percentile.'), self)
        self.by_issue = Bars(self.card(1, 0, 'Response time by issue', 'Median minutes from call to done.'), self)
        self.languages = Bars(self.card(1, 1, 'Languages', 'Language detected in each spoken request.'), self, 150)
        self.hours = Columns(self.card(2, 0, 'Requests by hour of day', "When patients call, in the server's local time.", wide=True), self)
        self.daily_card = self.card(3, 0, 'Requests per day', 'Hover a day for its median response time.', wide=True)
        self.daily = Columns(self.daily_card, self)
        self.terms = self.card(4, 0, 'Common words in requests', 'Words that appear in the most requests (English text).')
        slow = self.card(4, 1, 'Slowest responses', 'The five longest waits in this period.')
        style = ttk.Style(root)
        style.configure('Admin.Treeview', rowheight=28, font=f(9), background=ui.SURFACE, fieldbackground=ui.SURFACE, bordercolor=ui.SURFACE, borderwidth=0)
        style.configure('Admin.Treeview.Heading', font=f(8, True), background=ui.SURFACE, foreground='#898781', relief='flat', borderwidth=0)
        style.map('Admin.Treeview.Heading', background=[('active', ui.SURFACE)])
        style.layout('Admin.Treeview', [('Treeview.treearea', {'sticky': 'nswe'})])
        self.slowest = ttk.Treeview(slow, columns=('request', 'room', 'issue', 'department', 'response'), show='headings', height=5, style='Admin.Treeview')
        for column, heading, width in (('request', 'Request', 70), ('room', 'Room', 60), ('issue', 'Issue', 160), ('department', 'Department', 160), ('response', 'Response', 70)):
            self.slowest.heading(column, text=heading, anchor='w')
            self.slowest.column(column, width=width, minwidth=50, anchor='w', stretch=column in ('issue', 'department'))
        for chart in (self.issues, self.by_department, self.by_issue, self.languages, self.hours, self.daily):
            chart.pack(fill='x')
        self.slowest.pack(fill='x')
        self.updated = tk.Label(main, font=f(8), fg=ui.MUTED, bg=ui.PAGE)
        self.updated.pack(anchor='w', pady=(14, 0))
        self.paint_ranges()
        if refresh:
            self.load()

    def card(self, row, column, title, description, wide=False):
        card = tk.Frame(self.grid, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER, padx=18, pady=16)
        card.grid(row=row, column=column, columnspan=2 if wide else 1, sticky='nsew', padx=(0 if column == 0 else 16, 0), pady=(0, 16))
        tk.Label(card, text=title, font=self.f(11, True), fg=ui.INK, bg=ui.SURFACE).pack(anchor='w')
        tk.Label(card, text=description, font=self.f(8), fg=ui.MUTED, bg=ui.SURFACE).pack(anchor='w', pady=(2, 12))
        return card

    def paint_ranges(self):
        for days, b in self.range_buttons.items():
            on = days == self.days
            b.configure(bg='#e3edf8' if on else ui.SURFACE, fg=ui.INK if on else ui.INK_2, font=self.f(9, on))

    def choose(self, days):
        self.days = days
        self.paint_ranges()
        self.load()

    def load(self):
        if self.next:
            self.root.after_cancel(self.next)
        days = self.days
        self.bg.run(lambda: self.client.get('/api/admin/stats', days=days), lambda s: self.show(s) if s['days'] == self.days else None, self.error)
        self.next = self.root.after(REFRESH_MS, self.load)

    def error(self, text):
        self.status.configure(text='Hospital administration · not updated')
        self.grid.pack_forget()
        self.message.configure(text='⚠  ' + text, fg=ui.CRITICAL_EDGE)
        self.message.pack(fill='x', pady=(16, 0), before=self.updated)

    def name(self, code):
        return self.names.get(code, code)

    def show(self, s):
        t = s['totals']
        self.status.configure(text='Hospital administration')
        self.site.configure(text=f"Site {s['site']} · from the cloud archive · response time runs from the patient's call until staff mark the request done at the bedside.")
        agreement = s['agreement']
        agreed = f"{round(agreement['agreed'] / agreement['compared'] * 100)}%" if agreement['compared'] else '—'
        tiles = [('Requests', t['requests'], f"{t['open']} still open"), ('Median response', duration(t['median_minutes']), f"{t['resolved']} resolved"),
                 ('90th percentile', duration(t['p90_minutes']), '9 in 10 resolved within'),
                 ('Average rating', t['avg_rating'] if t['avg_rating'] is not None else '—', f"{t['ratings']} ratings (1–5)"),
                 ('Routing model agreed', agreed, f"with the clerk on {agreement['compared']} sent requests")]
        for labels, values in zip(self.tile_labels, tiles):
            for label, value in zip(labels, values):
                label.configure(text=str(value))
        self.updated.configure(text=f"Updated {time.strftime('%I:%M:%S %p', time.localtime(s['generated'])).lstrip('0')}. The archive is refreshed about every minute.")
        if not t['requests']:
            self.grid.pack_forget()
            self.message.configure(text='No requests in this period.', fg=ui.INK_2)
            self.message.pack(fill='x', pady=(16, 0), before=self.updated)
            return
        self.message.pack_forget()
        self.grid.pack(fill='x', pady=(16, 0), before=self.updated)
        self.issues.set(s['categories'], lambda d: d['count'], lambda d: d['name'], lambda d: str(d['count']),
                        lambda d: f"{d['name']}: {d['count']} requests · median response {duration(d['median_minutes'])}")
        timed = [d for d in s['departments'] if d['median_minutes'] is not None]
        self.by_department.set(timed, lambda d: d['median_minutes'], lambda d: d['name'], lambda d: duration(d['median_minutes']),
                               lambda d: f"{d['name']}: median {duration(d['median_minutes'])} · 90th percentile {duration(d['p90_minutes'])} · {d['resolved']} of {d['count']} resolved")
        issues = [d for d in s['categories'] if d['median_minutes'] is not None]
        self.by_issue.set(issues, lambda d: d['median_minutes'], lambda d: d['name'], lambda d: duration(d['median_minutes']),
                          lambda d: f"{d['name']}: median {duration(d['median_minutes'])} · 90th percentile {duration(d['p90_minutes'])}")
        self.languages.set(s['languages'][:8], lambda d: d['count'], lambda d: self.name(d['name']), lambda d: str(d['count']),
                           lambda d: f"{self.name(d['name'])}: {d['count']} requests")
        hours = [hour_label(h) for h in range(24)]
        self.hours.set(s['hours'], hours, 3, lambda i, v: f'{hours[i]}: {v} requests')
        if s['days'] > 1 and len(s['daily']) > 1:
            labels = [d['date'][5:] for d in s['daily']]
            self.daily.set([d['count'] for d in s['daily']], labels, -(-len(labels) // 10),
                           lambda i, v: f"{s['daily'][i]['date']}: {v} requests · median {duration(s['daily'][i]['median_minutes'])}")
            self.daily_card.grid()
        else:
            self.daily_card.grid_remove()
        for child in list(self.terms.winfo_children())[2:]:
            child.destroy()
        words = tk.Frame(self.terms, bg=ui.SURFACE)
        words.pack(anchor='w', fill='x')
        for i, term in enumerate(s['common_terms']):
            tk.Label(words, text=f"{term['term']}  {term['count']}", font=self.f(9), fg=ui.INK, bg=ui.SURFACE, padx=10, pady=4,
                     highlightthickness=1, highlightbackground=ui.BORDER).grid(row=i // 4, column=i % 4, sticky='w', padx=(0, 8), pady=(0, 8))
        if not s['common_terms']:
            tk.Label(words, text='Not enough text yet.', font=self.f(9), fg=ui.MUTED, bg=ui.SURFACE).grid(row=0, column=0)
        self.slowest.delete(*self.slowest.get_children())
        for r in s['slowest']:
            self.slowest.insert('', 'end', values=(f"#{r['request_id']}", r['room'], r['category'] or '—', r['department'], duration(r['minutes'])))

def main():
    ui.dpi_aware()
    root = tk.Tk()
    root.withdraw()
    client = ui.connect(root, 'Bello Admin')
    if client is None:
        root.destroy()
        return
    root.deiconify()
    Admin(root, client)
    root.mainloop()

if __name__ == '__main__':
    main()
