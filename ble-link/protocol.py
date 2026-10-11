"""Bello over Bluetooth LE: message format, chunking and retries. No Bluetooth code here.

The bedside unit (Pi) sends requests; the relay on the server machine answers them. Messages are split
into small chunks to fit BLE packets and carry a CRC32 so a damaged reassembly is dropped, not used.
Demo build: the radio link is NOT encrypted or authenticated. Anyone in Bluetooth range running this
protocol could read requests or send them. Add encryption before patient data goes over it.

Delivery: the relay acknowledges a request as soon as it has all of it, and answers when the server
replies. The Pi resends a request it hears nothing about. A resend reuses the same message id, so chunks
that arrived last time still count and only the missing ones need to get through. Requests also carry an
id, and the relay replays its cached answer instead of running a request twice, so a resend never
duplicates an upload.
"""
import asyncio
import json
import os
import struct
import time
import zlib

SERVICE_UUID = '7b1e0001-5c3a-4f7e-9d2b-6a8c1f4e2b90'
TO_SERVER_UUID = '7b1e0002-5c3a-4f7e-9d2b-6a8c1f4e2b90'   # Pi -> relay, notifications
TO_PI_UUID = '7b1e0003-5c3a-4f7e-9d2b-6a8c1f4e2b90'       # relay -> Pi, writes
# 180 bytes fits the MTU macOS usually negotiates (185); raise it once the self-test shows a larger one works.
CHUNK_SIZE = int(os.environ.get('BELLO_BLE_CHUNK', '180'))
HEADER = struct.Struct('<HHH')   # message id, chunk index, chunk count
PARTIAL_TIMEOUT = 30             # seconds a half-received message is kept
REPLY_CACHE_SECONDS = 300
ECHO_PATH = '__echo'             # answered by the relay itself, for the self-test

class LinkError(Exception):
    pass

class BadMessage(Exception):
    pass

def encode(meta, body=b''):
    head = json.dumps(meta, separators=(',', ':')).encode()
    data = struct.pack('<I', len(head)) + head + body
    return struct.pack('<I', zlib.crc32(data)) + data

def decode(blob):
    if len(blob) < 8:
        raise BadMessage('Message too short')
    data = blob[4:]
    if struct.unpack_from('<I', blob)[0] != zlib.crc32(data):
        raise BadMessage('Checksum mismatch (damaged in transit)')
    size = struct.unpack_from('<I', data)[0]
    try:
        meta = json.loads(data[4:4 + size])
    except ValueError:
        raise BadMessage('Unreadable message header') from None
    return meta, data[4 + size:]

def split(msg_id, blob, size=CHUNK_SIZE):
    room = size - HEADER.size
    count = max(1, -(-len(blob) // room))
    if count > 0xFFFF:
        raise LinkError('Message too large for the Bluetooth link')
    return [HEADER.pack(msg_id, i, count) + blob[i * room:(i + 1) * room] for i in range(count)]

class Assembler:
    """Reassembles chunks into messages. Duplicates are ignored, order does not matter, stale parts expire."""
    def __init__(self, timeout=PARTIAL_TIMEOUT):
        self.timeout = timeout
        self.parts = {}

    def add(self, chunk, now=None):
        now = time.monotonic() if now is None else now
        if len(chunk) < HEADER.size:
            return None
        msg_id, index, count = HEADER.unpack_from(chunk)
        for key in [k for k, v in self.parts.items() if now - v['at'] > self.timeout]:
            del self.parts[key]
        entry = self.parts.get(msg_id)
        if entry is None or entry['count'] != count:
            entry = self.parts[msg_id] = {'count': count, 'chunks': {}, 'at': now}
        if index >= count:
            return None
        entry['chunks'][index] = chunk[HEADER.size:]
        entry['at'] = now
        if len(entry['chunks']) < count:
            return None
        del self.parts[msg_id]
        return b''.join(entry['chunks'][i] for i in range(count))

class Endpoint:
    """What both ends share: message ids, and sending chunks one message at a time."""
    def __init__(self, send_chunk, chunk_size=CHUNK_SIZE):
        self.send_chunk, self.chunk_size = send_chunk, chunk_size
        self.assembler = Assembler()
        self.next_id = int.from_bytes(os.urandom(2), 'little')
        self.lock = None

    def new_id(self):
        self.next_id = (self.next_id + 1) & 0xFFFF
        return self.next_id

    async def send(self, meta, body=b'', msg_id=None):
        """Send one message. Pass the same msg_id to resend it, so the receiver can complete a partial copy."""
        if self.lock is None:
            self.lock = asyncio.Lock()
        blob = encode(meta, body)
        async with self.lock:
            for chunk in split(self.new_id() if msg_id is None else msg_id, blob, self.chunk_size):
                await self.send_chunk(chunk)

    def receive(self, chunk):
        """A message (meta, body) once complete, else None. Damaged messages are dropped."""
        blob = self.assembler.add(chunk)
        if blob is None:
            return None
        try:
            return decode(blob)
        except BadMessage as error:
            print(f'Bluetooth: dropped a message ({error})', flush=True)
            return None

class Requester(Endpoint):
    """The bedside side: request() sends and waits for the answer, resending when nothing is heard."""
    def __init__(self, send_chunk, chunk_size=CHUNK_SIZE, step=8.0, bytes_per_second=2000):
        super().__init__(send_chunk, chunk_size)
        self.step, self.rate = step, bytes_per_second
        self.waiting = {}
        self.resends = 0

    async def request(self, method, path, body=b'', content_type='application/json', timeout=150):
        rid = os.urandom(8).hex()
        meta = {'rid': rid, 'method': method, 'path': path, 'type': content_type}
        state = self.waiting[rid] = {'reply': asyncio.get_running_loop().create_future(), 'heard': False}
        deadline = time.monotonic() + timeout
        msg_id = self.new_id()
        # Before an acknowledgement, allow time for the transfer itself at a conservative rate.
        patience = self.step + len(body) / self.rate
        try:
            sends = 0
            while time.monotonic() < deadline:
                if state['heard']:
                    # The relay has the request and the server is working on it. Ask with a tiny status
                    # message rather than the whole request; the relay replies with its answer once ready.
                    await self.send({'rid': rid, 'poll': True})
                    wait = self.step
                else:
                    if sends:
                        self.resends += 1
                    sends += 1
                    await self.send(meta, body, msg_id)
                    wait = patience
                try:
                    return await asyncio.wait_for(asyncio.shield(state['reply']), min(wait, max(.1, deadline - time.monotonic())))
                except asyncio.TimeoutError:
                    pass
            raise LinkError('No answer from the Bello server over Bluetooth.')
        finally:
            self.waiting.pop(rid, None)

    def received(self, chunk):
        message = self.receive(chunk)
        if message is None:
            return
        meta, body = message
        state = self.waiting.get(meta.get('rid'))
        if state is None:
            return
        if meta.get('ack'):
            state['heard'] = True
        elif meta.get('unknown'):
            state['heard'] = False    # the relay lost it (for example, it restarted): send it again
        elif not state['reply'].done():
            state['reply'].set_result((meta['status'], meta.get('type', 'application/json'), body))

class Responder(Endpoint):
    """The relay side: answers each request once with handle(method, path, body, type) -> (status, type, body)."""
    def __init__(self, send_chunk, handle, chunk_size=CHUNK_SIZE):
        super().__init__(send_chunk, chunk_size)
        self.handle = handle
        self.replies = {}   # rid -> {'at', 'reply': (meta, body) or None while working}
        self.tasks = set()

    def received(self, chunk):
        message = self.receive(chunk)
        if message is not None:
            task = asyncio.ensure_future(self.answer(*message))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def answer(self, meta, body):
        now = time.monotonic()
        for rid in [r for r, v in self.replies.items() if now - v['at'] > REPLY_CACHE_SECONDS]:
            del self.replies[rid]
        rid = meta.get('rid')
        known = self.replies.get(rid)
        if meta.get('poll') and known is None:
            await self.send({'rid': rid, 'unknown': True})
            return
        if known is not None:
            if known['reply'] is None:
                await self.send({'rid': rid, 'ack': True})
            else:
                await self.send(*known['reply'], msg_id=known['msg_id'])
            return
        self.replies[rid] = {'at': now, 'reply': None}
        await self.send({'rid': rid, 'ack': True})
        if meta.get('path') == ECHO_PATH:
            status, kind, data = 200, 'application/octet-stream', body
        else:
            try:
                status, kind, data = await asyncio.to_thread(self.handle, meta['method'], meta['path'], body, meta.get('type', 'application/json'))
            except Exception as error:
                status, kind, data = 502, 'application/json', json.dumps({'error': f'Relay error: {type(error).__name__}'}).encode()
        reply = ({'rid': rid, 'status': status, 'type': kind}, data)
        msg_id = self.new_id()
        self.replies[rid] = {'at': time.monotonic(), 'reply': reply, 'msg_id': msg_id}
        await self.send(*reply, msg_id=msg_id)
