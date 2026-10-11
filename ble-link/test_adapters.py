"""The Bluetooth adapters' wiring, against stand-ins for `bless` (Pi) and `bleak` (Mac).

This checks that each adapter registers the right service and characteristics, sends with the right
calls and hands received chunks to the protocol. It cannot check the radio: that is the self-test's job.
"""
import asyncio
import sys
import types
import unittest
from unittest.mock import patch
import protocol

class FakeCharacteristic:
    def __init__(self, uuid):
        self.uuid, self.value = uuid, None

class FakeBlessServer:
    instance = None
    def __init__(self, name, loop):
        FakeBlessServer.instance = self
        self.name, self.characteristics, self.services, self.updates, self.started = name, {}, [], [], False
    async def add_new_service(self, uuid):self.services.append(uuid)
    async def add_new_characteristic(self, service, uuid, properties, value, permissions):self.characteristics[uuid] = FakeCharacteristic(uuid)
    async def start(self):self.started = True
    async def is_connected(self):return True
    def get_characteristic(self, uuid):return self.characteristics[uuid]
    def update_value(self, service, uuid):self.updates.append(bytes(self.characteristics[uuid].value))

fake_bless = types.SimpleNamespace(BlessServer=FakeBlessServer,
    GATTCharacteristicProperties=types.SimpleNamespace(read=1, notify=2, write=4, write_without_response=8),
    GATTAttributePermissions=types.SimpleNamespace(readable=1, writeable=2))

class PiAdapterTests(unittest.TestCase):
    def test_advertises_service_and_round_trips(self):
        with patch.dict(sys.modules, {'bless': fake_bless}):
            import pi_link
            link = pi_link.PiLink().start()
        server = FakeBlessServer.instance
        self.assertTrue(server.started)
        self.assertEqual(server.services, [protocol.SERVICE_UUID])
        self.assertEqual(set(server.characteristics), {protocol.TO_SERVER_UUID, protocol.TO_PI_UUID})
        # Play the relay: answer whatever the Pi notifies by writing to TO_PI.
        relay = protocol.Responder(None, lambda m, p, b, t: (200, 'application/json', b'{"ok": 1}'))
        async def write_back(chunk):
            link.on_write(server.characteristics[protocol.TO_PI_UUID], bytearray(chunk))
        relay.send_chunk = write_back
        def notified():
            while server.updates:
                chunk = server.updates.pop(0)
                link.loop.call_soon_threadsafe(relay.received, chunk)
        original = server.update_value
        def update_and_deliver(service, uuid):
            original(service, uuid); notified()
        server.update_value = update_and_deliver
        self.assertEqual(link.request('GET', '/api/requests', timeout=10), (200, 'application/json', b'{"ok": 1}'))

class RelayStartupTests(unittest.TestCase):
    def link(self, states):
        with patch.dict(sys.modules, {'bless': fake_bless}):
            import pi_link
            link = pi_link.PiLink().start()
        FakeBlessServer.instance.is_connected = lambda: asyncio.sleep(0, next(states))
        return pi_link, link

    def test_request_waits_for_a_relay_that_connects_shortly_after_startup(self):
        # The app asks for the bed's open requests the moment it starts, seconds before the relay's scan finds the Pi.
        pi_link, link = self.link(iter([False, False, False, True] + [True] * 20))
        link.requester.request = lambda *a: asyncio.sleep(0, (200, 'application/json', b'{"open": 0}'))
        with patch('pi_link.CONNECT_WAIT', 10), patch('pi_link.time.sleep', lambda s: None):
            self.assertEqual(link.request('GET', '/api/rooms/open?room=1', timeout=5)[0], 200)

    def test_request_gives_up_when_no_relay_ever_connects(self):
        pi_link, link = self.link(iter([False] * 1000))
        with patch('pi_link.CONNECT_WAIT', 0):
            with self.assertRaisesRegex(protocol.LinkError, 'not connected over Bluetooth'):
                link.request('GET', '/api/rooms/open?room=1', timeout=5)

class FakeBleakClient:
    instance = None
    def __init__(self, device, disconnected_callback):
        FakeBleakClient.instance = self
        self.disconnect = lambda: disconnected_callback(self)
        self.written, self.notify = [], None
        self.services = types.SimpleNamespace(get_characteristic=lambda uuid: types.SimpleNamespace(max_write_without_response_size=244))
    async def __aenter__(self):return self
    async def __aexit__(self, *a):pass
    async def write_gatt_char(self, uuid, data, response):self.written.append((uuid, bytes(data), response))
    async def start_notify(self, uuid, callback):self.notify = (uuid, callback)

class RelayAdapterTests(unittest.TestCase):
    def test_connects_forwards_and_writes_back(self):
        async def find(filterfunc, timeout):
            device = types.SimpleNamespace(name='Bello bedside', address='AA')
            assert filterfunc(device, types.SimpleNamespace(service_uuids=[protocol.SERVICE_UUID.upper()]))
            assert not filterfunc(device, types.SimpleNamespace(service_uuids=['180d']))
            return device
        fake_bleak = types.SimpleNamespace(BleakClient=FakeBleakClient, BleakScanner=types.SimpleNamespace(find_device_by_filter=find))
        import relay
        forwarded = []
        def forward(method, path, body, kind):
            forwarded.append((method, path, body));return 201, 'application/json', b'{"request_id": 5}'
        async def go():
            with patch.dict(sys.modules, {'bleak': fake_bleak}), patch.object(relay, 'forward', forward):
                task = asyncio.ensure_future(relay.serve_once())
                await asyncio.sleep(.05)
                client = FakeBleakClient.instance
                uuid, callback = client.notify
                self.assertEqual(uuid, protocol.TO_SERVER_UUID)
                # Play the Pi: send a request as notifications, collect the relay's writes.
                pi = protocol.Requester(None, 180)
                async def notify(chunk):callback(None, bytearray(chunk))
                pi.send_chunk = notify
                pending = asyncio.ensure_future(pi.request('POST', '/api/requests', b'{"room": "1"}', timeout=5))
                for _ in range(100):
                    await asyncio.sleep(.02)
                    while client.written:
                        uuid, data, response = client.written.pop(0)
                        self.assertEqual((uuid, response), (protocol.TO_PI_UUID, False))
                        self.assertLessEqual(len(data), 180)
                        pi.received(data)
                    if pending.done():break
                result = await pending
                client.disconnect(); await task
                return result
        self.assertEqual(asyncio.run(go()), (201, 'application/json', b'{"request_id": 5}'))
        self.assertEqual(forwarded, [('POST', '/api/requests', b'{"room": "1"}')])

if __name__ == '__main__':
    unittest.main()
