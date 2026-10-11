import asyncio
import random
import unittest
import protocol as p

class FakeRadio:
    """Connects a Requester and a Responder in one event loop, optionally losing, repeating and reordering chunks."""
    def __init__(self, loss=0.0, duplicate=0.0, reorder=0.0, seed=1):
        self.random = random.Random(seed)
        self.loss, self.duplicate, self.reorder = loss, duplicate, reorder
        self.held = {}
        self.sent = 0

    def wire(self, deliver):
        async def send(chunk):
            self.sent += 1
            if self.random.random() < self.loss:
                return
            copies = 2 if self.random.random() < self.duplicate else 1
            for _ in range(copies):
                if self.random.random() < self.reorder and deliver not in self.held:
                    self.held[deliver] = chunk   # deliver after the next chunk
                    continue
                deliver(chunk)
                if deliver in self.held:
                    deliver(self.held.pop(deliver))
        return send

def pair(handle, radio=None, chunk=60, step=0.3):
    radio = radio or FakeRadio()
    box = {}
    requester = p.Requester(radio.wire(lambda c: box['responder'].received(c)), chunk, step=step, bytes_per_second=1e9)
    box['responder'] = p.Responder(radio.wire(requester.received), handle, chunk)
    return requester, box['responder'], radio

class ProtocolTests(unittest.TestCase):
    def test_split_and_reassemble_in_any_order(self):
        blob = bytes(range(256)) * 20
        for size in (20, 60, 180, 512):
            chunks = p.split(7, blob, size)
            self.assertTrue(all(len(c) <= size for c in chunks))
            random.Random(size).shuffle(chunks)
            a = p.Assembler(); results = [a.add(c) for c in chunks + chunks[:2]]
            self.assertEqual([r for r in results if r is not None], [blob])

    def test_stale_parts_expire(self):
        a = p.Assembler(timeout=5)
        chunks = p.split(1, b'x' * 300, 60)
        a.add(chunks[0], now=0)
        self.assertIsNone(a.add(chunks[1], now=10))
        self.assertEqual(set(a.parts), {1})
        self.assertEqual(len(a.parts[1]['chunks']), 1)

    def test_damaged_message_is_dropped(self):
        blob = bytearray(p.encode({'rid': 'a'}, b'body'))
        blob[-1] ^= 1
        with self.assertRaises(p.BadMessage):p.decode(bytes(blob))
        self.assertEqual(p.decode(p.encode({'rid': 'a'}, b'body')), ({'rid': 'a'}, b'body'))

    def test_request_and_echo(self):
        calls = []
        def handle(method, path, body, kind):
            calls.append((method, path, body, kind));return 201, 'application/json', b'{"ok": true}'
        async def go():
            requester, _, _ = pair(handle)
            reply = await requester.request('POST', '/api/requests', b'{"room": "412"}')
            echo = await requester.request('POST', p.ECHO_PATH, b'z' * 5000, 'application/octet-stream')
            return reply, echo
        reply, echo = asyncio.run(go())
        self.assertEqual(reply, (201, 'application/json', b'{"ok": true}'))
        self.assertEqual(echo[2], b'z' * 5000)
        self.assertEqual(calls, [('POST', '/api/requests', b'{"room": "412"}', 'application/json')])

    def test_lossy_link_retries_without_repeating_work(self):
        calls = []
        def handle(method, path, body, kind):
            calls.append(path);return 202, 'application/json', b'{"state": "Queued"}'
        async def go():
            requester, _, radio = pair(handle, FakeRadio(loss=.15, duplicate=.1, reorder=.2, seed=3))
            results = [await requester.request('POST', f'/api/requests/{i}/audio', bytes(2000), 'audio/ogg', timeout=30) for i in range(5)]
            return results, requester.resends
        results, resends = asyncio.run(go())
        self.assertTrue(all(r[0] == 202 for r in results))
        self.assertGreater(resends, 0)                                 # the loss really happened
        self.assertEqual(calls, [f'/api/requests/{i}/audio' for i in range(5)])   # yet each upload ran once

    def test_slow_server_is_acknowledged_not_repeated(self):
        import time
        calls = []
        def handle(method, path, body, kind):
            calls.append(path);time.sleep(1);return 200, 'application/json', b'{}'
        async def go():
            requester, _, _ = pair(handle, step=.2)
            return await requester.request('GET', '/api/i18n/yue', timeout=10)
        self.assertEqual(asyncio.run(go())[0], 200)
        self.assertEqual(calls, ['/api/i18n/yue'])

    def test_concurrent_requests(self):
        def handle(method, path, body, kind):
            return 200, 'text/plain', path.encode()
        async def go():
            requester, _, _ = pair(handle)
            return await asyncio.gather(*(requester.request('GET', f'/r/{i}') for i in range(8)))
        self.assertEqual([r[2] for r in asyncio.run(go())], [f'/r/{i}'.encode() for i in range(8)])

    def test_relay_restart_mid_request(self):
        import time
        calls = []
        def handle(method, path, body, kind):
            calls.append(path);time.sleep(.5);return 200, 'application/json', b'{}'
        async def go():
            requester, responder, radio = pair(handle, step=.2)
            task = asyncio.ensure_future(requester.request('GET', '/slow', timeout=10))
            await asyncio.sleep(.1)
            responder.replies.clear()   # the relay forgot everything, as after a restart
            return await task
        self.assertEqual(asyncio.run(go())[0], 200)

    def test_no_answer_gives_up(self):
        async def go():
            async def nowhere(chunk):pass
            requester = p.Requester(nowhere, 60, step=.1)
            await requester.request('GET', '/api/requests', timeout=.5)
        with self.assertRaises(p.LinkError):asyncio.run(go())

    def test_server_error_status_passes_through(self):
        async def go():
            requester, _, _ = pair(lambda *a: (409, 'application/json', b'{"error": "Already transcribing"}'))
            return await requester.request('POST', '/api/requests/1/audio', b'x')
        self.assertEqual(asyncio.run(go())[0], 409)

if __name__ == '__main__':
    unittest.main()
