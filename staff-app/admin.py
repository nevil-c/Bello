"""Bello admin analytics: the most common patient requests and response times, from the cloud archive.

Response time runs from the patient's call until staff mark the request done at the bedside.
The numbers come from the server (GET /api/admin/stats), which reads the AWS archive.

  BELLO_SERVER=http://<server>:8766 python admin.py
"""
import time
from datetime import datetime
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

class AdminScrollFrame(ui.ScrollFrame):
    """Bound scrolling to the content and use small pixel steps."""
    def __init__(self, parent, **kw):
        super().__init__(parent, **kw)
        self.canvas.configure(yscrollincrement=1)

    def wheel(self, event):
        if not self.winfo_exists():
            return
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
        offset = min(max(0, height - self.canvas.winfo_height()), max(0, self.canvas.canvasy(0) + pixels))
        self.canvas.yview_moveto(offset / height if height else 0)
        return 'break'

class ChartPopup:
    """A delayed, stationary popup shared by the admin charts."""
    def __init__(self, root, f):
        self.root, self.key, self.pending = root, None, None
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.configure(bg='#9bb5ce')
        self.label = tk.Label(self.window, bg='#f4f8fc', fg='#243448', font=f(10),
                              justify='left', wraplength=360, padx=14, pady=10)
        self.label.pack(padx=1, pady=1)
        root.bind('<MouseWheel>', self.hide, add='+')
        root.bind('<Button-4>', self.hide, add='+')
        root.bind('<Button-5>', self.hide, add='+')
        root.bind('<Configure>', lambda e: self.hide() if e.widget is root else None, add='+')
        root.bind('<Unmap>', self.hide, add='+')

    def show(self, chart, text):
        key = (chart, text)
        if self.key == key:
            return
        self.hide()
        self.key = key
        self.pending = self.root.after(180, lambda: self.reveal(chart, text))

    def reveal(self, chart, text):
        self.pending = None
        if self.key != (chart, text) or not chart.winfo_exists():
            return
        self.label.configure(text=text)
        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        px, py = chart.winfo_pointerxy()
        # Place below the pointer, or above when near the bottom edge.
        screen_width, screen_height = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x = max(8, min(px + 18, screen_width - width - 8))
        y = py + 24
        if y + height > screen_height - 8:
            y = max(8, py - height - 24)
        self.window.geometry(f'{width}x{height}+{x}+{y}')
        self.window.deiconify()
        self.window.lift()

    def hide(self, event=None):
        if self.pending is not None:
            self.root.after_cancel(self.pending)
            self.pending = None
        self.key = None
        self.window.withdraw()

class Chart(tk.Canvas):
    """Hover uses a stationary popup, shared across each logical data point."""
    def __init__(self, parent, app, height):
        super().__init__(parent, bg=ui.SURFACE, highlightthickness=0, height=height)
        self.app, self.data = app, None
        self.details = {}
        self.bind('<Configure>', lambda e: self.redraw())
        self.bind('<Motion>', self.motion)
        self.bind('<Leave>', self.clear_detail)
        self.bind('<MouseWheel>', self.clear_detail, add='+')

    def set(self, *data):
        self.data = data
        self.redraw()

    def hover(self, item, text):
        self.details[item] = text

    def motion(self, event):
        current = self.find_withtag('current')
        text = self.details.get(current[-1]) if current else None
        if text:
            self.app.popup.show(self, text)
        else:
            self.clear_detail()

    def clear_detail(self, event=None):
        if self.app.popup.key is not None and self.app.popup.key[0] is self:
            self.app.popup.hide()

    def redraw(self):
        self.details.clear()
        self.clear_detail()
        self.delete('all')
        if self.data is not None and self.winfo_width() > 50:
            self.draw(self.winfo_width(), *self.data)

class Bars(Chart):
    """Horizontal bars, one series: label on the left, value at the end of the bar."""
    ROW = 38

    def __init__(self, parent, app, label_width=190):
        super().__init__(parent, app, 60)
        self.label_width = label_width

    def draw(self, width, items, value, label, value_text, tip):
        f = self.app.f
        shown = items[:5]
        self.configure(height=350)
        if not shown:
            self.create_text(16, 30, text='No data for this period', anchor='w', font=f(10), fill=ui.MUTED)
            return
        top = max([value(d) or 0 for d in shown] + [1])
        for i, d in enumerate(shown):
            y, v = i * 70, value(d) or 0
            hit = self.create_rectangle(0, y, width, y + 70, fill=ui.SURFACE, outline='')
            self.hover(hit, tip(d))
            before = set(self.find_all())
            self.create_text(0, y + 4, text=label(d), width=width * .44, anchor='nw', font=f(9), fill=ui.INK_2)
            self.create_text(width - 4, y + 4, text=value_text(d), width=width * .54, anchor='ne', justify='right', font=f(9), fill=ui.INK)
            self.create_rectangle(0, y + 48, width - 4, y + 60, fill='#edf2f7', outline='')
            bar = self.create_rectangle(0, y + 48, max(0, v / top * (width - 4)), y + 60, fill=ui.SERIES, outline='')
            self.hover(bar, tip(d))
            for item in set(self.find_all()) - before:
                self.hover(item, tip(d))


class Columns(Chart):
    """Columns over an ordered axis (hours or days) with hairline gridlines."""
    def __init__(self, parent, app):
        super().__init__(parent, app, 200)

    def draw(self, width, values, labels, every, tip):
        f, height, left, bottom, head = self.app.f, 240, 56, 30, 42
        self.configure(height=height)
        self.create_text(0, 10, text="Requests (count)", anchor="w", font=f(9), fill=ui.INK_2)
        if not values or not any(values):
            self.create_text(width / 2, 110, text='No requests in this period', font=f(10), fill=ui.MUTED)
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
                value_label = self.create_text(x + band / 2, head + plot_h - v / top * plot_h - 10, text=str(v), font=f(9, True), fill=ui.INK)
                self.hover(value_label, tip(i, v))
            if i % every == 0:
                axis_label = self.create_text(x + band / 2, height - 8, text=labels[i], font=f(8), fill='#898781')
                self.hover(axis_label, tip(i, v))

class Admin:
    def __init__(self, root, client, refresh=True):
        self.root, self.client = root, client
        base_font = ui.setup(root, 'Bello Admin Analytics', '1440x900')
        self.f = f = lambda size, bold=False: base_font(max(13, size + 3), bold)
        self.status = ui.header(root, f, 'Hospital Dashboard', 'Loading…', centered=True)
        self.bg = ui.Background(root)
        self.popup = ChartPopup(root, f)
        self.names = ui.language_names(client) if client else {'en': 'English'}
        self.days, self.next = 7, None
        self.loaded_days, self.load_id = None, 0

        scroll = self.scroll = AdminScrollFrame(root, bg=ui.PAGE)
        scroll.pack(fill='both', expand=True)
        main = tk.Frame(scroll.inner, bg=ui.PAGE, padx=28, pady=22)
        main.pack(fill='both', expand=True)
        top = tk.Frame(main, bg=ui.PAGE)
        top.pack(fill='x', pady=(0, 18))
        titles = tk.Frame(top, bg=ui.PAGE)
        titles.pack(side='left')
        tk.Label(titles, text='Patient requests and response times', font=base_font(28), fg=ui.INK, bg=ui.PAGE).pack(anchor='w', pady=(4, 2))
        period_bar = tk.Frame(main, bg=ui.PAGE)
        period_bar.pack(fill='x', pady=(0, 18))
        ranges = tk.Frame(period_bar, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER, padx=3, pady=3)
        ranges.pack(side='left', anchor='n')
        period_info = tk.Frame(period_bar, bg=ui.PAGE)
        period_info.pack(side='left', fill='x', expand=True, padx=(20, 0))
        self.period_dates = tk.Label(period_info, text='Reporting period: loading…', font=f(10, True), fg=ui.INK, bg=ui.PAGE, justify='left', wraplength=800)
        self.period_dates.pack(anchor='w')
        self.period_updated = tk.Label(period_info, text='Last updated: —', font=f(9), fg=ui.MUTED, bg=ui.PAGE, justify='left', wraplength=800)
        self.period_updated.pack(anchor='w', pady=(5, 0))
        style = ttk.Style(root)
        style.configure('Period.TButton', font=f(10), padding=(16, 12), foreground=ui.INK_2, background=ui.SURFACE)
        style.configure('SelectedPeriod.TButton', font=f(10, True), padding=(16, 12), foreground='white', background='#244f7a')
        style.map('SelectedPeriod.TButton', background=[('pressed', '#183754'), ('active', '#1d4268')], foreground=[('active', 'white')])
        style.map('Period.TButton', background=[('active', '#e3edf8')])
        self.range_buttons = {}
        for days, text in ((1, 'Today'), (7, '7 days'), (30, '30 days')):
            b = ttk.Button(ranges, text=text, style='Period.TButton', cursor='hand2',
                          command=lambda d=days: self.choose(d))
            b.pack(side='left', padx=1)
            self.range_buttons[days] = b

        self.message = tk.Label(main, text='Loading analytics…', font=f(10), fg=ui.INK_2,
                                bg=ui.PAGE, height=2, anchor='w', justify='left', wraplength=1250)
        self.message.pack(fill='x', pady=(0, 10))
        self.tiles = tk.Frame(main, bg=ui.PAGE)
        self.tiles.pack(fill='x')
        self.tile_labels = []
        for i in range(5):
            column = (0, 3, 2, 1, 4)[i]
            tile = tk.Frame(self.tiles, bg=ui.SURFACE, highlightthickness=1, highlightbackground=ui.BORDER, padx=16, pady=14)
            tile.grid(row=0, column=column, sticky='nsew', padx=(0 if column == 0 else 12, 0))
            self.tiles.columnconfigure(i, weight=1, uniform='tile')
            labels = [tk.Label(tile, font=f(9), fg=ui.INK_2, bg=ui.SURFACE, wraplength=230, justify='left'), tk.Label(tile, font=f(20, True), fg=ui.INK, bg=ui.SURFACE),
                      tk.Label(tile, font=f(8), fg=ui.MUTED, bg=ui.SURFACE, wraplength=200, justify='left')]
            for label in labels[:2]:
                label.pack(anchor='w')
            self.tile_labels.append(labels)

        self.grid = tk.Frame(main, bg=ui.PAGE)
        self.grid.pack(fill='x', pady=(16, 0))
        self.grid.columnconfigure(0, weight=1, uniform='card')
        self.grid.columnconfigure(1, weight=1, uniform='card')
        self.issues = Bars(self.card(0, 0, 'Most common issues', 'Top 5 categories · request count and share of all requests.'), self)
        self.by_department = Bars(self.card(0, 1, 'Response time by department', 'Top 5 by volume · typical time and 9-in-10 completion time (minutes).'), self)
        self.by_issue = Bars(self.card(1, 0, 'Response time by issue', 'Top 5 by volume · typical time and 9-in-10 completion time (minutes).'), self)
        self.languages = Bars(self.card(1, 1, 'Languages', 'Top 5 spoken languages · request count and share of all requests.'), self, 150)
        self.hours = Columns(self.card(2, 0, 'Requests by hour of day', "When patients call, in the server's local time.", wide=True), self)
        self.daily_card = self.card(3, 0, 'Requests per day', 'Requests per date · counts shown above each column.', wide=True)
        self.daily = Columns(self.daily_card, self)
        self.terms = self.card(4, 0, 'Common words in requests', 'Words that appear in the most requests (English text).')
        slow = self.card(4, 1, 'Longest completed responses', 'Five completed requests with the longest response times; these are no longer waiting.')
        style = ttk.Style(root)
        style.configure('Admin.Treeview', rowheight=38, font=f(9), background=ui.SURFACE, fieldbackground=ui.SURFACE, bordercolor=ui.SURFACE, borderwidth=0)
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
        tk.Label(card, text=description, font=self.f(8), fg=ui.MUTED, bg=ui.SURFACE, wraplength=570, justify='left').pack(anchor='w', pady=(2, 12))
        return card

    def paint_ranges(self):
        for days, b in self.range_buttons.items():
            on = days == self.days
            b.configure(style='SelectedPeriod.TButton' if on else 'Period.TButton')

    def choose(self, days):
        self.days = days
        self.paint_ranges()
        self.load()

    def load(self):
        if self.next:
            self.root.after_cancel(self.next)
        days = self.days
        self.load_id += 1
        load_id = self.load_id
        period = 'Today' if days == 1 else f'{days} days'
        self.status.configure(text=f'Loading {period}…')
        self.period_updated.configure(text=f'Loading {period}… Previous results remain visible until ready.')
        self.message.configure(text=f'Loading {period}…' + (' Showing previous results until ready.' if self.loaded_days else ''), fg=ui.INK_2)
        def done(stats):
            if load_id == self.load_id:
                self.show(stats)
        def failed(message):
            if load_id == self.load_id:
                self.error(message)
        if self.client is None:
            failed('No server connected. Connect to Bello to change the reporting period.')
        else:
            self.bg.run(lambda: self.client.get('/api/admin/stats', days=days), done, failed)
        self.next = self.root.after(REFRESH_MS, self.load)

    def error(self, text):
        self.status.configure(text='Hospital administration · not updated')
        self.period_updated.configure(text='Refresh failed · showing the last successful reporting period.')
        period = 'Today' if self.loaded_days == 1 else f'{self.loaded_days} days'
        retained = f' Showing the last successful results ({period}).' if self.loaded_days is not None else ''
        self.message.configure(text='⚠  ' + text + retained, fg=ui.CRITICAL_EDGE, wraplength=1100)


    def name(self, code):
        return self.names.get(code, code)

    def show(self, s):
        offset = self.scroll.canvas.canvasy(0)
        self._show(s)
        self.root.update_idletasks()
        height = max(self.scroll.inner.winfo_reqheight(), self.scroll.canvas.winfo_height())
        maximum = max(0, height - self.scroll.canvas.winfo_height())
        self.scroll.canvas.yview_moveto(min(maximum, max(0, offset)) / height if height else 0)

    def _show(self, s):
        self.loaded_days = s['days']
        end = datetime.fromtimestamp(s['generated']).astimezone()
        start = datetime.fromtimestamp(s['generated'] - s['days'] * 86400).astimezone()
        self.period_dates.configure(text=f'{start:%b %d, %Y · %I:%M %p} – {end:%b %d, %Y · %I:%M %p %Z}')
        window = 'Last 24 hours' if s['days'] == 1 else f"Last {s['days']} days"
        self.period_updated.configure(text=f'{window} · Last updated {end:%b %d, %I:%M:%S %p %Z}')
        t = s['totals']
        self.status.configure(text='Hospital administration · ' + ('Today' if s['days'] == 1 else f"{s['days']} days"))
        agreement = s['agreement']
        agreed = f"{round(agreement['agreed'] / agreement['compared'] * 100)}%" if agreement['compared'] else '—'
        tiles = [('Requests', t['requests'], f"{t['open']} still open"), ('Median response', duration(t['median_minutes']), f"{t['resolved']} resolved"),
                 ('90th percentile', duration(t['p90_minutes']), '9 in 10 resolved within'),
                 ('Average rating', f"{t['avg_rating']:g}/5" if t['avg_rating'] is not None else '—/5', f"{t['ratings']} ratings (1–5)"),
                 ('Routing model agreed', agreed, f"with the clerk on {agreement['compared']} sent requests")]
        for labels, values in zip(self.tile_labels, tiles):
            for label, value in zip(labels, values):
                label.configure(text=str(value))
        self.updated.configure(text=f"Updated {time.strftime('%I:%M:%S %p', time.localtime(s['generated'])).lstrip('0')}. The archive is refreshed about every minute.")
        self.message.configure(text='No requests in this reporting period.' if not t['requests'] else 'Showing archive results for the reporting period above.', fg=ui.INK_2)
        total = max(1, t['requests'])
        self.issues.set(s['categories'], lambda d: d['count'], lambda d: d['name'], lambda d: f"{d['count']} requests · {d['count']/total:.0%}",
                        lambda d: f"{d['name']}: {d['count']} requests · median response {duration(d['median_minutes'])}")
        timed = [d for d in s['departments'] if d['median_minutes'] is not None]
        self.by_department.set(timed, lambda d: d['median_minutes'], lambda d: d['name'], lambda d: f"Typical {duration(d['median_minutes'])} · 9/10 within {duration(d['p90_minutes'])}",
                               lambda d: f"{d['name']}: median {duration(d['median_minutes'])} · 90th percentile {duration(d['p90_minutes'])} · {d['resolved']} of {d['count']} resolved")
        issues = [d for d in s['categories'] if d['median_minutes'] is not None]
        self.by_issue.set(issues, lambda d: d['median_minutes'], lambda d: d['name'], lambda d: f"Typical {duration(d['median_minutes'])} · 9/10 within {duration(d['p90_minutes'])}",
                          lambda d: f"{d['name']}: median {duration(d['median_minutes'])} · 90th percentile {duration(d['p90_minutes'])}")
        self.languages.set(s['languages'][:8], lambda d: d['count'], lambda d: self.name(d['name']), lambda d: f"{d['count']} requests · {d['count']/total:.0%}",
                           lambda d: f"{self.name(d['name'])}: {d['count']} requests")
        hours = [hour_label(h) for h in range(24)]
        self.hours.set(s['hours'], hours, 3, lambda i, v: f'{hours[i]}: {v} requests')
        if s['days'] > 1 and len(s['daily']) > 1:
            labels = [d['date'][5:] for d in s['daily']]
            self.daily.set([d['count'] for d in s['daily']], labels, -(-len(labels) // 10),
                           lambda i, v: f"{s['daily'][i]['date']}: {v} requests · median {duration(s['daily'][i]['median_minutes'])}")
            self.daily_card.grid()
        else:
            self.daily.set([d['count'] for d in s['daily']], [d['date'][5:] for d in s['daily']], 1, lambda i, v: f'{v} requests')
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
