"""Server-side Bluetooth LE relay (MacBook, CoreBluetooth, via `bleak`).

Finds the bedside unit by its Bello service, connects, and passes each request it receives to the Bello
server on this machine, sending the answer back. Reconnects whenever the link drops.

  BELLO_API_TOKEN=... python ble-link/relay.py
  BELLO_SERVER   the server to forward to (default http://127.0.0.1:8766)
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from protocol import CHUNK_SIZE, SERVICE_UUID, TO_PI_UUID, TO_SERVER_UUID, Responder

SERVER = os.environ.get('BELLO_SERVER', 'http://127.0.0.1:8766').rstrip('/')

def forward(method, path, body, content_type, server=SERVER, token=None):
    """One request to the Bello server: (status, content type, body). Runs in a worker thread."""
    token = token if token is not None else os.environ.get('BELLO_API_TOKEN', '')
    request = Request(server + path, data=body if method == 'POST' else None, method=method,
                      headers={'X-Bello-Token': token, 'Content-Type': content_type})
    try:
        with urlopen(request, timeout=300) as response:
            return response.status, response.headers.get('Content-Type', 'application/json'), response.read()
    except HTTPError as error:
        return error.code, error.headers.get('Content-Type', 'application/json'), error.read()
    except (URLError, OSError):
        return 502, 'application/json', json.dumps({'error': 'The relay cannot reach the Bello server. Is backend.py running?'}).encode()

def log(text):
    print(time.strftime('%H:%M:%S ') + text, flush=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The staff apps need a Python with Tkinter; this one runs the relay, which needs Bluetooth and no
# Tk. Override if your Tk Python lives elsewhere (BELLO_NO_APPS=1 just runs the relay).
TK_PYTHON = os.environ.get('BELLO_TK_PYTHON', '/opt/homebrew/bin/python3')

def open_apps():
    """Open the care queue and the department inbox beside the relay: one command starts the demo."""
    if os.environ.get('BELLO_NO_APPS'):
        log('BELLO_NO_APPS set: not opening the staff apps.')
        return
    python = TK_PYTHON if os.path.exists(TK_PYTHON) else sys.executable
    for name in ('clerk.py', 'inbox.py'):
        try:
            subprocess.Popen([python, os.path.join(ROOT, 'staff-app', name)], env=dict(os.environ, BELLO_SERVER=SERVER))
        except OSError as error:
            log(f'Could not open {name}: {error}')
            return
    log(f'Opened the care queue and the department inbox ({python} → {SERVER})')

async def serve_once():
    from bleak import BleakClient, BleakScanner
    log('Looking for the bedside unit…')
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: SERVICE_UUID in [u.lower() for u in ad.service_uuids], timeout=20)
    if device is None:
        return
    gone = asyncio.Event()
    async with BleakClient(device, disconnected_callback=lambda client: gone.set()) as client:
        target = client.services.get_characteristic(TO_PI_UUID)
        limit = getattr(target, 'max_write_without_response_size', CHUNK_SIZE) or CHUNK_SIZE
        size = min(CHUNK_SIZE, limit)
        responder = Responder(lambda chunk: client.write_gatt_char(TO_PI_UUID, chunk, response=False), forward, size)
        await client.start_notify(TO_SERVER_UUID, lambda _, data: responder.received(bytes(data)))
        log(f'Connected to {device.name or device.address} (chunks of {size} bytes). Forwarding to {SERVER}')
        await gone.wait()
    log('Bedside unit disconnected')

async def main():
    if not os.environ.get('BELLO_API_TOKEN'):
        sys.exit('Set BELLO_API_TOKEN to the token the Bello server was started with.')
    open_apps()
    while True:
        try:
            await serve_once()
        except Exception as error:
            log(f'Bluetooth error: {type(error).__name__}: {error}')
        await asyncio.sleep(2)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
