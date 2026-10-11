# Local recording → transcript → department inbox

No ElevenLabs account, API key, or cloud transcription is used.

## Install on Raspberry Pi 4B (8 GB)

Use 64-bit Raspberry Pi OS (aarch64), Python 3.9+ supported by the installed package wheels, and a USB microphone. Install python3-venv, python3-tk and libportaudio2 using the OS package manager. Use active cooling for sustained CPU work.

From the repository root:

```sh
python3 -m venv patient-app/.venv-local
patient-app/.venv-local/bin/python -m pip install -r patient-app/requirements.txt -r hospital-dashboard/requirements.txt
patient-app/.venv-local/bin/python hospital-dashboard/prepare_model.py
patient-app/.venv-local/bin/python patient-app/run_local.py
```

The explicit download requires internet once. Runtime loads only the local model directory and never downloads a model or uploads audio externally. Models are not committed to Git. Start with multilingual tiny; compare base only after measuring actual Pi latency and accuracy. Configure BELLO_MODEL_DIR for another downloaded directory, BELLO_STT_THREADS (default 3), BELLO_ROOM and BELLO_BED as needed. CPU INT8, beam size 1, one model instance and one transcription worker are used. No GPU is required. Mac performance does not predict Raspberry Pi performance.

The launcher starts a matching local backend and patient window and prints the clerk-dashboard URL. The private Bello upload token still protects API endpoints; no speech-provider key is needed. Direct backend launch: `patient-app/.venv-local/bin/python hospital-dashboard/backend.py --port 8766`.

## API behavior

POST /api/requests creates a room-linked request; optional text creates a typed request without STT. POST /api/requests/{id}/audio accepts raw WAV or MP3 (10 MB maximum) and returns HTTP 202 with state Queued. GET /api/requests/{id} returns Queued, Transcribing, Ready to send, Retry required or Delivered to queue, plus transcript/language/error. Requests and recordings persist in SQLite and transcript-data. A single worker processes jobs; at most four pending/running jobs are admitted. Queue overflow returns 429 without saving the upload. Duplicate queued/completed/delivered uploads return 409.

The patient and command-line uploader poll until the transcript is saved. Patient polling stops after ten minutes but that does not stop backend processing; check the clerk dashboard before submitting again. Cancellation stops recording or local polling, not a job already received by the backend. No automatic retries. On restart, interrupted jobs are marked Retry required and their recordings retained. To retry explicitly, re-upload the saved recording for the same request with ingest_audio.py. No duplicate department delivery is created.

The original-language transcript is saved; English translation is not enabled. The clerk reviews new requests before routing. Clinical classification, urgency prediction, external phone calls and service-completion signals are not implemented. Typed messages, department acknowledgment, and one satisfaction response per request retain their existing APIs. Feedback is optional and patient initiated after receiving help.

## Validation

Run `python3 -m unittest discover -s hospital-dashboard -p 'test_*.py'`. Automated workflow tests use a fake recognizer; real recognition must also be tested. Benchmark 5/10/20-second recordings on the actual Pi, tracking latency, RAM, temperature, silence, background noise and language-specific mistakes. Do not infer clinical accuracy from a successful transcription.

Only one backend can use a data directory at a time: a process lock rejects a second instance on Linux/macOS. Back up the existing database before migration. The schema adds a nullable error column; no existing transcript, recording, feedback or delivery is deleted.

## Verified on development Mac

The previous environment used Python 3.13.0a4, which cannot load the NumPy wheels used here. A separate stable Python 3.12 environment was created at patient-app/.venv-local; the older environment was preserved. On this Mac launch with `cd ~/Desktop/Bello-repo/patient-app` then `./.venv-local/bin/python run_local.py`. Use stable Python (not an alpha build) on the Pi.

Local verification: faster-whisper 1.2.1, CTranslate2 4.8.2, NumPy 2.2.6 and PyAV 16.1.0. PyAV is constrained below 17 because a newer installed decoder removed an argument used by this faster-whisper release. A spoken blanket request transcribed correctly in approximately 0.47 seconds with 234 MiB peak process memory on this Mac. These are single-clip development measurements, not Pi benchmarks or accuracy validation. A temporary-database HTTP test verified recording -> queued job -> original transcript -> department delivery -> acknowledgment -> feedback with offline model mode enabled.

Benchmark on the Pi: `patient-app/.venv-local/bin/python hospital-dashboard/benchmark_stt.py /path/to/request.wav`. Measure CPU temperature separately, for example with the Pi's system monitoring tools, and compare repeated runs after warm-up. Original-language and noisy/quiet speech accuracy still require representative recordings.

### Live listening text
The patient screen displays provisional local transcripts while recording. A cumulative WAV snapshot is offered every three seconds after speech starts. One background worker shares the cached model, gives completed recordings priority, and replaces pending snapshots rather than accumulating them. Partial text may change; only the final recording produces a saved, dispatchable transcript. Silence or failure in a partial does not fail the final upload. Temporary snapshots expire after 60 seconds and are held in memory (maximum four sessions). This is incremental transcription, not guaranteed per-word streaming; latency on a Raspberry Pi 4 must be measured on that device.
