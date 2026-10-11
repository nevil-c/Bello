"""The bedside unit's Bluetooth LE link (Raspberry Pi, BlueZ, via `bless`).

The Pi advertises the Bello service and waits for the relay on the server to connect. Requests go out
as notifications on TO_SERVER; answers come back as writes on TO_PI. voice_client.api() calls request()
from ordinary threads; the Bluetooth work runs on this module's own asyncio loop.

  BELLO_TRANSPORT=ble python patient-app/nurse_call.py
"""
import asyncio
import os
import threading
import time
from protocol import CHUNK_SIZE, SERVICE_UUID, TO_PI_UUID, TO_SERVER_UUID, LinkError, Requester

NAME = os.environ.get('BELLO_BLE_NAME', 'Bello bedside')
CONNECT_WAIT = float(os.environ.get('BELLO_BLE_CONNECT_WAIT', '30'))   # seconds a request waits for the relay

class PiLink:
    def __init__(self, chunk_size=CHUNK_SIZE):
        self.chunk_size = chunk_size
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.error = None
        self.server = None
        self.requester = None
        self.was_connected = None

    def start(self):
        threading.Thread(target=self.run, name='bello-ble', daemon=True).start()
        if not self.ready.wait(20):
            raise LinkError('Bluetooth did not start within 20 seconds.')
        if self.error:
            raise LinkError(f'Bluetooth could not start: {self.error}')
        return self

    def run(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.setup())
        except Exception as error:
            self.error = f'{type(error).__name__}: {error}'
        finally:
            self.ready.set()
        if not self.error:
            self.loop.create_task(self.watch())
            self.loop.run_forever()

    async def setup(self):
        from bless import BlessServer, GATTAttributePermissions, GATTCharacteristicProperties
        self.server = BlessServer(name=NAME, loop=self.loop)
        self.server.read_request_func = lambda characteristic, **kw: characteristic.value
        self.server.write_request_func = self.on_write
        await self.server.add_new_service(SERVICE_UUID)
        await self.server.add_new_characteristic(SERVICE_UUID, TO_SERVER_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify, None, GATTAttributePermissions.readable)
        await self.server.add_new_characteristic(SERVICE_UUID, TO_PI_UUID,
            GATTCharacteristicProperties.write | GATTCharacteristicProperties.write_without_response, None, GATTAttributePermissions.writeable)
        await self.server.start()
        self.requester = Requester(self.notify, self.chunk_size)
        print(f'Bluetooth: advertising as "{NAME}"; waiting for the server relay', flush=True)

    async def notify(self, chunk):
        self.server.get_characteristic(TO_SERVER_UUID).value = bytearray(chunk)
        self.server.update_value(SERVICE_UUID, TO_SERVER_UUID)
        await asyncio.sleep(float(os.environ.get('BELLO_BLE_PACE_MS', '0')) / 1000)

    def on_write(self, characteristic, value, **kwargs):
        if str(characteristic.uuid).lower() == TO_PI_UUID:
            data = bytes(value)
            self.loop.call_soon_threadsafe(self.requester.received, data)

    async def connected_now(self):
        try:
            return bool(await self.server.is_connected())
        except Exception:
            return True   # backend cannot tell; let the request's own timeout decide

    async def watch(self):
        while True:
            state = await self.connected_now()
            if state != self.was_connected:
                print('Bluetooth: server relay connected' if state else 'Bluetooth: server relay not connected', flush=True)
                self.was_connected = state
            await asyncio.sleep(2)

    def connected(self):
        return asyncio.run_coroutine_threadsafe(self.connected_now(), self.loop).result(5)

    def request(self, method, path, body=b'', content_type='application/json', timeout=150):
        """(status, content type, body) from the server, or LinkError. Safe to call from any thread."""
        # The relay scans for up to 20 s before it connects, and again after any drop, so the first
        # requests at startup (and any made during a reconnect) wait for it rather than failing.
        if not self.wait_connected(CONNECT_WAIT):
            raise LinkError('The Bello server is not connected over Bluetooth. Check that the relay is running on the server.')
        future = asyncio.run_coroutine_threadsafe(self.requester.request(method, path, body or b'', content_type, timeout), self.loop)
        try:
            return future.result(timeout + 5)
        except TimeoutError:
            future.cancel()
            raise LinkError('No answer from the Bello server over Bluetooth.') from None

    def wait_connected(self, seconds):
        end = time.monotonic() + seconds
        while True:
            if self.connected():
                return True
            if time.monotonic() >= end:
                return False
            time.sleep(.5)
