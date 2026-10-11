"""Bedside microphone check: real level numbers, and which gate is refusing the patient.

    python3 mic_check.py                            # 8 seconds; speak the way the patient does
    BELLO_MIC_CHECK_SECONDS=15 python3 mic_check.py

Runs the app's own capture path (voice_client's device/rate choice, 100 ms blocks) and its own
speech gate, so what it prints is exactly what the bedside screen sees. One line per block, then a
verdict naming the gate that refused it and the value to set. Set BELLO_INPUT_DEVICE to test
another microphone by index. No server and no window are opened.
"""
import os
import sys
import threading
import time

import numpy as np

from voice_client import MicrophonePCM, microphone_settings
from speech_activity import SpeechActivity
from noise_suppression import NoiseSuppressor


def input_devices(sd):
    print('Input devices (BELLO_INPUT_DEVICE picks one by index):')
    for index, device in enumerate(sd.query_devices()):
        if device['max_input_channels']:
            print(f"  {index:>3}  {device['name']}  {int(device['default_samplerate'])} Hz  "
                  f"{int(device['max_input_channels'])} ch")


def verdict(rows, threshold):
    """rows: (rms, vad votes, tonal, speech, rms after the noise gate), one per 100 ms block."""
    peak = max(rms for rms, *_ in rows) if rows else 0.0
    passed = [rms for rms, _, _, speech, _ in rows if speech]
    print()
    print(f'{len(rows)} blocks, loudest {peak:.0f} RMS, gate needs {threshold:.0f}.')
    if not rows:
        print('Nothing was captured at all: the stream opened but delivered no audio.')
        return 1
    if passed:
        typical = float(np.median(passed))
        print(f'{len(passed)} blocks passed the gate — this microphone works; typical speech is '
              f'{typical:.0f} RMS.')
        print(f'If partial text is still thin, lower the level gate to:  '
              f'export BELLO_MIN_SPEECH_RMS={max(20, round(typical * .35))}')
        return 0
    voiced = sum(1 for _, votes, _, _, _ in rows if votes >= 4)
    hum = sum(1 for _, _, tonal, _, _ in rows if tonal)
    why = []
    if peak < 50:
        why.append('almost no signal is arriving from this device at all (wrong input, muted, or unplugged)')
    else:
        if peak < threshold:
            why.append(f'the loudest block ({peak:.0f}) never reaches the {threshold:.0f} level gate')
        if voiced < len(rows) * .2:
            why.append(f'the voice detector called only {voiced}/{len(rows)} blocks speech '
                       '(too far from the mic, or a quiet mic that mode 3 cannot hear)')
        if hum > len(rows) * .5:
            why.append(f'{hum}/{len(rows)} blocks were rejected as a steady tone (hum or hiss)')
    print('No block passed the speech gate: ' + '; '.join(why) + '.')
    if peak >= 50:
        print(f'Try:  export BELLO_MIN_SPEECH_RMS={max(20, round(peak * .35))}')
    print('Both the endpointing AND the noise gate follow that verdict, so a refused block uploads '
          'near-silence — that is why it reads as "not picking up at all".')
    return 1


def main():
    try:
        import sounddevice as sd
    except ImportError:
        print('Microphone support is missing. Install patient-app/requirements.txt.', file=sys.stderr)
        return 1
    seconds = float(os.environ.get('BELLO_MIC_CHECK_SECONDS', '8'))
    input_devices(sd)
    try:
        device, rate = microphone_settings(sd)
    except RuntimeError as error:
        print(f'\nNo usable microphone: {error}')
        return 1
    print(f"\nUsing {sd.query_devices(device, 'input')['name']} (index {device}) at {rate} Hz.")
    print(f'Speak the way the patient does, for {seconds:.0f} seconds.\n')
    print('  time    rms  vad  tonal    gate   after-gate rms')
    activity = SpeechActivity()
    suppressor = NoiseSuppressor()
    pcm = MicrophonePCM(rate)
    rows = []
    with sd.RawInputStream(device=device, samplerate=rate, channels=1, dtype='int16',
                           blocksize=rate // 10) as stream:
        started = time.monotonic()
        while time.monotonic() - started < seconds:
            raw = pcm.read(stream, threading.Event())
            activity.accept(raw)
            cleaned = suppressor.process(raw, activity.speech)
            after = float(np.sqrt(np.mean(np.frombuffer(cleaned, dtype='<i2').astype(float) ** 2)))
            rows.append((activity.rms, activity.votes, activity.tonal, activity.speech, after))
            print(f"{activity.elapsed:6.1f}s {activity.rms:6.0f}  {activity.votes}/5  "
                  f"{str(activity.tonal):>5}  {'SPEECH' if activity.speech else '  --  '}  {after:6.0f}")
    return verdict(rows, activity.min_rms)


if __name__ == '__main__':
    sys.exit(main())
