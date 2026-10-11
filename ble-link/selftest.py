"""Bluetooth link self-test, run on the Pi while the relay runs on the server.

Sends echo payloads of increasing size and reports round-trip time, throughput and resends. The relay
answers these itself, so the Bello server does not need to be running.

  python ble-link/selftest.py
"""
import os
import sys
import time
from pi_link import PiLink

def main():
    link = PiLink().start()
    print('Waiting up to 60 s for the relay to connect…', flush=True)
    if not link.wait_connected(60):
        sys.exit('The relay did not connect. Is `python ble-link/relay.py` running on the server, with Bluetooth allowed for Terminal?')
    for size in (1_000, 10_000, 30_000, 100_000):
        payload = os.urandom(size)
        before = link.requester.resends
        start = time.monotonic()
        status, _, body = link.request('POST', '__echo', payload, 'application/octet-stream', timeout=120)
        seconds = time.monotonic() - start
        ok = status == 200 and body == payload
        print(f'{size // 1000:>4} KB  {"ok " if ok else "BAD"}  {seconds:6.2f} s round trip  '
              f'{2 * size / 1024 / seconds:6.1f} KB/s  resends {link.requester.resends - before}', flush=True)
    print('A 10-second spoken request is about 30 KB compressed: use the 30 KB line to estimate the delay.')

if __name__ == '__main__':
    main()
