# Bello over Bluetooth

Connects one bedside Raspberry Pi to the Bello server on a MacBook over Bluetooth Low Energy, so neither needs the hospital Wi-Fi.

```
Pi: nurse_call.py → voice_client ──Bluetooth LE──▶ MacBook: relay.py → backend.py (127.0.0.1:8766)
                                                             clerk.py / inbox.py / admin.py → 127.0.0.1
```

- **Pi:** advertises the Bello service (`pi_link.py`, using `bless` on BlueZ).
- **MacBook:** the relay finds the Pi, connects, and passes each request to the server on the same Mac (`relay.py`, using `bleak` on CoreBluetooth). It reconnects by itself if the link drops.
- **Messages:** split into small chunks with a checksum. Lost pieces are resent, and a resend never repeats an upload (`protocol.py`).
- **Audio:** recordings go Opus-compressed, about 3 KB per second of speech instead of 32.
- **Live partial text:** off in this mode. The patient sees "Processing your recording", then the transcript.

**Demo only: the Bluetooth link is not encrypted.** Anyone within range running this protocol could read or send requests. Add encryption before real patient data goes over it.

## MacBook Air (server)

From the repository root, in the server's environment:

```bash
hospital-dashboard/.venv/bin/python -m pip install -r ble-link/requirements-mac.txt
```

Every time, use three Terminal windows. Run `export BELLO_API_TOKEN=pick-a-long-random-token` in each first, or once in your shell profile.

1. **Server:** listens on this Mac only.

```bash
hospital-dashboard/.venv/bin/python hospital-dashboard/backend.py --host 127.0.0.1
```

2. **Relay:**

```bash
hospital-dashboard/.venv/bin/python ble-link/relay.py
```

The first time, macOS asks whether Terminal may use Bluetooth: allow it. If you said no, turn it on in System Settings → Privacy & Security → Bluetooth.

3. **Clerk dashboard:**

```bash
BELLO_SERVER=http://127.0.0.1:8766 python3 staff-app/clerk.py
```

Keep the MacBook awake: sleep stops the server and the relay. Either keep the lid open and run `caffeinate -dims` in a spare Terminal, or set System Settings → Battery → Options → "Prevent automatic sleeping when the display is off" while it's plugged in.

## Raspberry Pi (bedside)

One-time setup, with the patient app already installed (`patient-app/.venv-local`):

```bash
patient-app/.venv-local/bin/python -m pip install -r ble-link/requirements-pi.txt
```

```bash
sudo rfkill unblock bluetooth
```

Every time:

```bash
BELLO_TRANSPORT=ble BELLO_ROOM=412 BELLO_BED=B patient-app/.venv-local/bin/python patient-app/nurse_call.py
```

The Pi needs no server address or token in this mode. Its terminal prints `Bluetooth: advertising as "Bello bedside"`, then `Bluetooth: server relay connected` once the relay finds it.

## First test: the self-test

Before using the patient app, check the link itself. With the relay running on the Mac (the server doesn't need to be running for this), run on the Pi:

```bash
patient-app/.venv-local/bin/python ble-link/selftest.py
```

It sends 1, 10, 30 and 100 KB back and forth and prints the time, speed and resends for each. A 10-second spoken request is about 30 KB, so the 30 KB line is roughly the extra delay Bluetooth adds. Send me the output; it tells us whether to tune anything.

Before that, check the Pi can compress audio:

```bash
patient-app/.venv-local/bin/python -c "import av; av.codec.Codec('libopus','w'); print('Opus OK')"
```

(If it fails, the app falls back to another encoder automatically, but the recordings will be larger.)

## Troubleshooting

| Symptom | Try |
|---|---|
| Pi: `Bluetooth could not start` (D-Bus or permission errors) | `sudo systemctl status bluetooth` should say active; add your user to the group with `sudo usermod -aG bluetooth $USER` and log in again |
| Relay keeps printing "Looking for the bedside unit…" | The Pi app or self-test must be running. Turn Bluetooth off and on on the Mac. Move the devices closer. Run `bluetoothctl show` on the Pi and check it says `Powered: yes` |
| Relay connects then drops repeatedly | Interference or distance. Try `BELLO_BLE_CHUNK=120` on both ends |
| Self-test shows many resends | Set `BELLO_BLE_PACE_MS=5` on the Pi (a short pause between chunks) |
| Self-test is reliable but slow | Try `BELLO_BLE_CHUNK=240` on both ends (only if the relay's "chunks of N bytes" line shows at least 240) |
| Patient screen: "The Bello server is not connected over Bluetooth" | The relay isn't connected; check its window |
| Patient screen: "The relay cannot reach the Bello server" | Start `backend.py` on the Mac, with the same `BELLO_API_TOKEN` as the relay |

## Tests

These run anywhere; no Bluetooth needed:

```bash
python -m unittest discover -s ble-link -p 'test_*.py'
```

`test_end_to_end.py` also needs PyAV and numpy (the server environment has them).
