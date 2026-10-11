"""The patient app in Bluetooth mode, through the protocol and the relay's forwarding, to the real server.

Only the radio is simulated (with some lost, repeated and reordered chunks). Speech recognition,
translation and routing are stand-ins. Needs PyAV and numpy (for the Opus compression).
"""
import asyncio
import functools
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / 'patient-app'), str(HERE.parent / 'hospital-dashboard')]
os.environ['BELLO_DATA_DIR'] = tempfile.mkdtemp()
os.environ['BELLO_API_TOKEN'] = 'test-token'

import backend
import decision
import protocol
import relay
import translation
import voice_client
from test_protocol import FakeRadio

class SimulatedLink:
    """PiLink's request() over a simulated radio to a Responder that forwards like the real relay."""
    def __init__(self, server):
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        radio = FakeRadio(loss=.03, duplicate=.05, reorder=.1, seed=7)
        handle = functools.partial(relay.forward, server=server, token='test-token')
        box = {}
        self.requester = protocol.Requester(radio.wire(lambda c: box['r'].received(c)), 180, step=1, bytes_per_second=1e6)
        box['r'] = protocol.Responder(radio.wire(self.requester.received), handle, 180)

    def request(self, method, path, body=b'', content_type='application/json', timeout=150):
        return asyncio.run_coroutine_threadsafe(self.requester.request(method, path, body or b'', content_type, timeout), self.loop).result(timeout + 5)

def recording(seconds=3):
    import numpy as np
    t = np.arange(16000 * seconds) / 16000
    pcm = (3000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(pcm.tobytes())
    return out.getvalue()

class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.heard = []
        def transcribe(audio, ext):
            cls.heard.append(audio[:4])
            return {'text': '我想要多一張被', 'language': 'yue'}
        backend.transcribe = transcribe
        translation.to_english = lambda text, lang: 'I want another blanket'
        decision.classify = lambda text: {'category': 'Room Environment / Cleaning', 'department': 'Housekeeping & Facilities',
                                          'urgency': 2, 'urgency_confidence': .6, 'more_info_needed': False, 'scores': {}}
        backend.initialize()
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), backend.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        threading.Thread(target=backend.worker, args=(backend.STOP,), daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'
        voice_client.TRANSPORT = 'ble'
        voice_client._link = SimulatedLink(cls.url)

    @classmethod
    def tearDownClass(cls):
        backend.STOP.set(); cls.server.shutdown(); cls.server.server_close()

    def test_patient_flow_over_bluetooth(self):
        result = voice_client.submit_audio(recording(), '412 · B', threading.Event())
        self.assertEqual(result['transcript'], '我想要多一張被')
        self.assertEqual(self.heard[-1], b'OggS')   # compressed on the Pi, accepted and decoded by the server
        with backend.db() as c:
            row = c.execute('SELECT audio, recommended FROM requests WHERE id=?', (result['request_id'],)).fetchone()
        self.assertTrue(row['audio'].endswith('.ogg'))
        typed = voice_client.submit_text('Can I have some water?', '412 · B')
        self.assertEqual(typed['state'], 'Classifying')
        self.assertEqual(voice_client.open_requests('412 · B'), 2)
        self.assertEqual(voice_client.resolve_room('412 · B')['resolved'], 2)
        self.assertEqual(voice_client.submit_feedback(result['request_id'], 5, 'yes')['request_id'], result['request_id'])
        self.assertEqual(voice_client.ui_strings('yue')['yes'], '係')

    def test_server_errors_reach_the_patient_app(self):
        with self.assertRaisesRegex(RuntimeError, 'Room is required'):
            voice_client.api('/api/requests', json.dumps({'room': ''}).encode())

    def test_relay_reports_a_stopped_server(self):
        status, _, body = relay.forward('GET', '/api/requests', b'', 'application/json', server='http://127.0.0.1:9', token='t')
        self.assertEqual(status, 502);self.assertIn('backend.py', json.loads(body)['error'])

if __name__ == '__main__':
    unittest.main()
