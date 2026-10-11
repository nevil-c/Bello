"""Staff apps: the server client, the live feed, the clerk's row logic, and that each window builds and updates.

The window tests open Tk windows briefly: run where a display is available.
"""
import json
import threading
import time
import tkinter as tk
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
import admin
import bello_api
import clerk
import inbox

def request(**fields):
    base = dict(id=1, room='412 · B', recommended='Housekeeping & Facilities', transcript='我想要多一張被', transcript_en='I want another blanket',
                language='yue', ui_language='yue', category='Room Environment / Cleaning', urgency=2, urgency_confidence=.6,
                more_info_needed=0, review=None, state='Ready to send', error=None, created=time.time() - 300, resolved=None,
                called=None, delivered_department=None, delivery_status=None)
    base.update(fields)
    return base

class Server(BaseHTTPRequestHandler):
    """A stand-in Bello server: one stream event then a dropped connection, or a 500 when `broken`."""
    broken = False
    polled = 0
    def log_message(self, *a): pass
    def do_GET(self):
        if self.headers.get('X-Bello-Token') != 't':
            self.send_response(401); self.end_headers(); return
        if self.path == '/api/stream':
            if Server.broken:
                self.send_response(500); self.end_headers(); return
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.write(b': keep-alive\n\ndata: ' + json.dumps({'requests': [request()]}).encode() + b'\n\n'); return
        Server.polled += 1
        body = json.dumps({'requests': [request(id=2)]}).encode()
        self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

class ApiTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Server)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        Server.broken, Server.polled = False, 0

    def updates(self, feed, until, seconds=8):
        got, end = [], time.monotonic() + seconds
        while time.monotonic() < end and not until(got):
            try:got.append(feed.updates.get(timeout=.2))
            except Exception:pass
        feed.stop.set()
        return got

    def test_events_are_parsed_and_comments_skipped(self):
        lines = [': keep-alive', '', 'data: {"requests": [1]}', '', 'data: {"requests": []}', '']
        self.assertEqual(list(bello_api.parse_events(lines)), [{'requests': [1]}, {'requests': []}])

    def test_stream_then_polling_after_three_failures(self):
        feed = bello_api.LiveRequests(bello_api.Client(self.url, 't')); feed.start()
        got = self.updates(feed, lambda g: ('requests', [request()]) in g)
        self.assertIn(('status', 'Live'), got)
        Server.broken = True
        feed = bello_api.LiveRequests(bello_api.Client(self.url, 't'))
        with patch.object(feed.stop, 'wait', lambda s: time.sleep(.01)):
            feed.start()
            got = self.updates(feed, lambda g: ('status', 'Live (polling)') in g)
        self.assertEqual([k for k, v in got if v == 'Reconnecting…'], ['status'] * 3)
        self.assertEqual(next(v for k, v in got if k == 'requests')[0]['id'], 2)

    def test_bad_token(self):
        with self.assertRaises(bello_api.Unauthorized):bello_api.Client(self.url, 'wrong').get('/api/requests')
        with self.assertRaises(bello_api.ApiError):bello_api.Client('http://127.0.0.1:9', 't').get('/api/requests')

class RowLogicTests(unittest.TestCase):
    def test_badges_and_status(self):
        self.assertEqual([clerk.level(u)[0] for u in (None, 1, 3, 5, 7)], ['Not assessed', 'Low', 'Routine', 'High', 'Urgent'])
        r = request(delivered_department='Food Services', called=time.time(), resolved=time.time())
        self.assertEqual(clerk.delivery_text(r).splitlines()[0], '✓ Sent to Food Services')
        self.assertIn('Resolved in', clerk.time_text(r));self.assertFalse(clerk.sendable(r))
        self.assertTrue(clerk.translated(request()));self.assertFalse(clerk.translated(request(language='en', transcript_en='I want another blanket')))
        self.assertFalse(clerk.visible(request(transcript=None, state='Awaiting recording')))
        self.assertTrue(clerk.visible(request(transcript=None, state='Transcribing')))

    def test_ordering_puts_unassessed_with_urgent(self):
        rows = [request(id=1, urgency=2), request(id=2, urgency=None), request(id=3, urgency=7), request(id=4, urgency=8)]
        self.assertEqual([r['id'] for r in clerk.ordered(rows, 'urgency', -1)], [4, 2, 3, 1])
        rooms = [request(id=1, room='210 · B'), request(id=2, room='9 · A'), request(id=3, room='100 · A')]
        self.assertEqual([r['id'] for r in clerk.ordered(rooms, 'room', 1)], [2, 3, 1])
        self.assertTrue(clerk.matches(request(), 'blanket'));self.assertFalse(clerk.matches(request(), 'water'))

class WindowTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk(); self.root.geometry('+3000+100')
    def tearDown(self):
        for job in self.root.tk.call('after', 'info'):
            self.root.after_cancel(job)
        self.root.destroy()
    def pump(self):
        for _ in range(5):self.root.update()

    def test_clerk_updates_rows_in_place_and_sends(self):
        app = clerk.Clerk(self.root, None, live=False); app.names = {'yue': 'Cantonese', 'en': 'English'}
        app.requests = [request(), request(id=2, urgency=7, transcript='Chest pain', transcript_en='Chest pain', language='en', review='Check')]
        app.render(); self.pump()
        self.assertEqual(list(app.rows), [2, 1]);first = app.rows[1]
        self.assertEqual(first.team.get(), 'Housekeeping & Facilities')
        first.team.current(0); first.selected(); self.pump()
        self.assertFalse(first.send.winfo_ismapped())
        self.assertTrue(first.team.winfo_ismapped())
        first.team.current(2); first.selected(); self.pump()
        self.assertTrue(first.send.winfo_ismapped())
        self.assertEqual(first.send.label.cget('text'), 'Send + call')
        app.requests = [request(state='Delivered to queue', delivered_department='Housekeeping & Facilities')]; app.render(); self.pump()
        self.assertIs(app.rows[1], first);self.assertNotIn(2, app.rows);self.assertFalse(first.send.winfo_ismapped())
        posted = []
        class Client:
            def post(self, path, body):posted.append((path, body));return {'department': body['department'], 'phone': '2200'}
        app.client = Client(); app.requests = [request()]; app.render(); app.chosen[1] = 'Food Services'
        app.dispatch(1, app.rows[1].send)
        end = time.monotonic() + 2
        while not posted and time.monotonic() < end:self.root.update()
        self.assertEqual(posted, [('/api/requests/1/dispatch', {'department': 'Food Services', 'call': True})])
        app.open_detail(1, call=True); self.pump()
        self.assertIn('Cantonese (original)', app.detail.original.cget('text'))
        self.assertTrue(app.detail.preview.winfo_ismapped())

    def test_patient_scroll_is_bounded_and_keeps_layout(self):
        from types import SimpleNamespace
        app = clerk.Clerk(self.root, None, live=False)
        app.requests = [request()]
        app.open_detail(1)
        self.pump()
        body = next(w for w in app.detail.winfo_children() if isinstance(w, clerk.PatientScrollFrame))
        canvas = body.canvas
        width = body.inner.winfo_width()
        original_wrap = app.detail.request.cget('wraplength')
        before = app.detail.bind('<MouseWheel>')
        with patch.object(body, 'winfo_containing', return_value=body.inner):
            for _ in range(100):
                body.wheel(SimpleNamespace(x_root=0, y_root=0, num=5, delta=0))
            self.pump()
            self.assertLessEqual(canvas.canvasy(0), max(0, body.inner.winfo_reqheight() - canvas.winfo_height()) + 1)
            for _ in range(100):
                body.wheel(SimpleNamespace(x_root=0, y_root=0, num=4, delta=0))
            self.pump()
        self.assertEqual(canvas.canvasy(0), 0)
        self.assertEqual(body.inner.winfo_width(), width)
        self.assertEqual(app.detail.request.cget('wraplength'), original_wrap)
        body.destroy()
        self.assertTrue(before)
        self.assertFalse(app.detail.bind('<MouseWheel>'))

    def test_inbox_cards(self):
        app = inbox.Inbox(self.root, None, refresh=False)
        app.show(app.team.get(), [{'id': 'a' * 32, 'room': '412 · B', 'transcript': 'Blanket please', 'language': 'en', 'status': 'Delivered to queue', 'created': time.time()}])
        self.pump()
        self.assertEqual(len(app.cards.winfo_children()), 1);self.assertEqual(app.team.get(), 'Housekeeping & Facilities')

    def test_admin_range_changes_ignore_old_responses(self):
        app = admin.Admin(self.root, None, refresh=False)
        pending = []
        class Client:
            def get(self, path, days):
                return {'days': days}
        app.client = Client()
        app.bg.run = lambda work, done, failed: pending.append((work, done, failed))
        shown = []
        app.show = lambda stats: shown.append(stats['days'])
        app.choose(1)
        app.choose(30)
        app.choose(7)
        pending[2][1](pending[2][0]())
        pending[0][1](pending[0][0]())
        pending[1][2]('Old connection error')
        self.assertEqual(shown, [7])
        self.assertNotIn('Old connection error', app.message.cget('text'))
        self.assertEqual(app.days, 7)

    def test_admin_draws_every_card(self):
        app = admin.Admin(self.root, None, refresh=False)
        group = {'name': 'Toileting', 'count': 3, 'resolved': 2, 'median_minutes': 7.0, 'p90_minutes': 9.4}
        stats = {'site': 'unit-4', 'days': 7, 'generated': time.time(),
                 'totals': {'requests': 3, 'resolved': 2, 'open': 1, 'median_minutes': 7.0, 'p90_minutes': 9.4, 'avg_rating': 5, 'ratings': 1},
                 'categories': [group], 'departments': [dict(group, name='Primary Nurse')], 'languages': [{'name': 'yue', 'count': 3}],
                 'hours': [0] * 9 + [3] + [0] * 14, 'daily': [{'date': '2026-10-10', 'count': 1, 'median_minutes': 4}, {'date': '2026-10-11', 'count': 2, 'median_minutes': 9}],
                 'agreement': {'compared': 2, 'agreed': 1}, 'common_terms': [{'term': 'blanket', 'count': 3}],
                 'slowest': [{'request_id': 5, 'room': '412 · B', 'category': 'Toileting', 'department': 'Primary Nurse', 'minutes': 10.0}]}
        app.names = {'yue': 'Cantonese'}; app.show(stats); self.pump()
        self.assertEqual(app.tile_labels[1][1].cget('text'), '7 min');self.assertEqual(app.tile_labels[4][1].cget('text'), '50%')
        self.assertEqual(app.tile_labels[3][1].cget('text'), '5/5')
        self.assertTrue(all(not labels[2].winfo_ismapped() for labels in app.tile_labels))
        self.assertTrue(app.issues.find_all());self.assertTrue(app.daily.find_all());self.assertEqual(len(app.slowest.get_children()), 1)
        self.assertIsInstance(app.languages, admin.Bars)
        self.assertIsInstance(app.by_department, admin.Bars)
        self.assertIsInstance(app.by_issue, admin.Bars)
        self.assertIsInstance(app.daily, admin.Columns)
        chart = app.issues
        shape = next(iter(chart.details))
        chart.addtag_withtag('current', shape)
        before_height = chart.master.winfo_height()
        chart.motion(None); self.pump()
        self.assertIn('Toileting', app.popup.key[1])
        app.popup.reveal(chart, app.popup.key[1]); self.pump()
        popup_position = app.popup.window.geometry()
        chart.motion(None); self.pump()
        self.assertEqual(app.popup.window.geometry(), popup_position)
        self.assertIn('Toileting', app.popup.label.cget('text'))
        self.assertEqual(chart.master.winfo_height(), before_height)
        chart.clear_detail()
        self.assertIsNone(app.popup.key)
        self.assertFalse(app.popup.window.winfo_ismapped())
        chart.redraw()
        self.assertIsNone(app.popup.key)
        self.root.geometry('1280x700+100+100'); self.pump()
        app.scroll.canvas.yview_moveto(.4); self.pump()
        offset = app.scroll.canvas.canvasy(0)
        chart_height = app.issues.winfo_height()
        app.show(stats); self.pump()
        self.assertAlmostEqual(app.scroll.canvas.canvasy(0), offset, delta=2)
        self.assertEqual(app.issues.winfo_height(), chart_height)
        chart_text = [app.issues.itemcget(item, 'text') for item in app.issues.find_all() if app.issues.type(item) == 'text']
        self.assertTrue(any('requests' in text and '%' in text for text in chart_text))
        import copy
        empty = copy.deepcopy(stats)
        empty['totals'].update(requests=0, resolved=0, open=0)
        for field in ('categories', 'departments', 'languages', 'daily', 'common_terms', 'slowest'):
            empty[field] = []
        empty['hours'] = [0]*24
        app.show(empty); self.pump()
        self.assertIn('No requests', app.message.cget('text'))
        self.assertTrue(app.grid.winfo_ismapped())
        self.assertEqual(app.issues.winfo_height(), chart_height)
        app.show(stats); self.pump()
        app.error('Cloud archive unreachable (URLError).'); self.pump()
        self.assertTrue(app.grid.winfo_ismapped());self.assertIn('unreachable', app.message.cget('text'));self.assertIn('last successful', app.message.cget('text'))
        self.assertEqual(admin.duration(90), '1.5 h');self.assertEqual(admin.hour_label(13), '1p')

if __name__ == '__main__':
    unittest.main()
