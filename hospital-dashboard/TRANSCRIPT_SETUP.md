# LAN server setup: recording → transcript → English → routing → dashboards

No cloud speech service is used. Audio goes from the bedside units to the LAN server over the hospital network, and stays on that server. Only text records are copied to the optional AWS archive.

## Server (M5 Mac, 24 GB)

From the repository root, with a stable Python (3.10–3.12):

```sh
python3 -m venv hospital-dashboard/.venv
hospital-dashboard/.venv/bin/python -m pip install -r hospital-dashboard/requirements.txt
hospital-dashboard/.venv/bin/python hospital-dashboard/prepare_model.py                  # large-v3-turbo
hospital-dashboard/.venv/bin/python hospital-dashboard/prepare_model.py --model large-v3 # only for BELLO_EN_ENGINE=whisper
hospital-dashboard/.venv/bin/python hospital-dashboard/prepare_translation_model.py      # NLLB-200, about 2.5 GB
```

The routing checkpoint is read from `~/laya-out/ckpt/laya-patient`; train it as described in [../decision-model/README.md](../decision-model/README.md), or point `BELLO_LAYA_MODEL` elsewhere. Then start the server:

```sh
export BELLO_API_TOKEN=<a long random string shared with the bedside units and staff>
hospital-dashboard/.venv/bin/python hospital-dashboard/backend.py --host 0.0.0.0
```

macOS asks once whether Python may accept incoming connections; allow it. The startup lines say whether the routing and translation models loaded. Staff run the Python staff apps against it (`staff-app/clerk.py`, `inbox.py` and `admin.py`, with `BELLO_SERVER=http://<server>:8766`; see [../staff-app/README.md](../staff-app/README.md)) and sign in with the token. `--seed-demo` fills an empty database with the requests in `demo_requests.json`. They go through translation and routing, and they are never sent to the cloud archive.

| Variable | Default | Purpose |
|---|---|---|
| `BELLO_API_TOKEN` | required on the network | The shared token for bedside units and staff pages |
| `BELLO_WHISPER_MODEL` | `large-v3-turbo` | Whisper model under `models/` (`BELLO_MODEL_DIR` for any other folder) |
| `BELLO_STT_THREADS` | cores − 2, at most 8 | CPU threads for Whisper. CTranslate2 has no Apple GPU backend, so Whisper runs on CPU INT8 |
| `BELLO_EN_ENGINE` | `nllb` | `nllb`: NLLB translates the transcript. `whisper`: Whisper translates the audio, which needs `BELLO_WHISPER_MODEL=large-v3` (turbo was not trained to translate) |
| `BELLO_LAYA_MODEL` | `~/laya-out/ckpt/laya-patient` | Routing checkpoint (`BELLO_LAYA_DEVICE` to force `mps`/`cuda`/`cpu`) |
| `BELLO_TRANSLATION_MODEL_DIR` | `models/nllb-200-distilled-600M` | NLLB folder (`BELLO_TRANSLATION_DEVICE` to force a device) |
| `BELLO_DATA_DIR` | `transcript-data/` | SQLite database, recordings and the UI-translation cache |
| `BELLO_CLOUD_URL`, `BELLO_CLOUD_API_KEY`, `BELLO_SITE_ID` | off | The AWS archive, and the admin dashboard's data ([../aws-backend](../aws-backend/README.md)) |

**Choosing the English engine.** Run `hospital-dashboard/.venv/bin/python hospital-dashboard/benchmark_translate.py --say` on the server. It speaks Cantonese, Mandarin, Spanish and English phrases with macOS voices, then prints both engines' text and timings side by side. Judge them by meaning, especially the clinical phrases. In development on CPU, NLLB rendered 我心口好翳，有啲頭暈 ("my chest feels tight, I'm a little dizzy") as "My mouth is full, my head is full". That is why the clerk dashboard labels every translated request and shows the original text next to it.

**Screen languages.** The bedside Language setting changes the on-screen text only. The spoken language is always detected by Whisper. `GET /api/i18n/<language>` returns the screen strings. A table committed in `patient-app/` wins; Cantonese has one (`strings_yue.json`) because NLLB's Cantonese UI strings were wrong in testing ("Do you want to submit?" came out as "Do you want to surrender?"). Any other language is translated by NLLB once, then cached in `transcript-data/i18n/`. These are unreviewed machine translations; commit a reviewed `strings_<code>.json` to replace one. NLLB-200 is licensed CC-BY-NC 4.0 (non-commercial).

## Bedside units (Raspberry Pi)

Install only the patient app (see [../patient-app/README.md](../patient-app/README.md)), plus `fonts-noto-cjk` so Chinese text renders, then point it at the server:

```sh
export BELLO_SERVER=http://<server>:8766 BELLO_API_TOKEN=<the shared token> BELLO_ROOM=412 BELLO_BED=B
patient-app/.venv-local/bin/python patient-app/nurse_call.py
```

`patient-app/run_local.py` still runs everything on one machine for development (tiny Whisper by default, and a fresh token it prints for the dashboards).

## API

Every `/api/` call needs the `X-Bello-Token` header.

| Call | Purpose |
|---|---|
| `POST /api/requests` | Create a request for `room`, with the screen's `ui_language`. Optional `text` makes a typed request; `confirmed_empty: true` makes an empty one, which goes straight to clerk review |
| `POST /api/requests/{id}/audio` | Raw WAV or MP3 (10 MB maximum); returns 202 `Queued`. At most four jobs are admitted; more returns 429 |
| `POST /api/requests/{id}/preview` | A WAV snapshot for live partial text while the patient speaks |
| `GET /api/requests/{id}` | State and original transcript, polled by the bedside unit |
| `GET /api/requests` | Every request with its translation, routing and timings |
| `GET /api/stream` | The same list as server-sent events, pushed on every change (the clerk app falls back to polling if the stream keeps failing) |
| `POST /api/requests/{id}/dispatch` | `{department, call}`: deliver to a department inbox; `call: true` also logs the department call (placeholder) |
| `POST /api/rooms/resolve` | `{room}`: staff marked the bed's requests done; resolves every open request for that room and bed |
| `GET /api/rooms/open?room=` | How many requests for the bed are still open |
| `GET /api/i18n/{language}` | Bedside screen strings |
| `GET /api/languages` | The 100 supported languages (code, name) |
| `GET /api/admin/stats?days=1\|7\|30` | Admin analytics computed from the AWS archive (cached for a minute) |
| `GET /api/departments/queue?department=`, `POST /api/deliveries/{id}/acknowledge`, `POST /api/requests/{id}/feedback`, `GET /api/requests/{id}/history` | Department inbox, acknowledgment, patient feedback, history |

States: `Awaiting recording` → `Queued` → `Transcribing` → `Classifying` (the transcript is saved and shown to the patient) → `Ready to send` → `Delivered to queue`. A failure gives `Retry required`. On restart, interrupted transcriptions become `Retry required`, with the recording kept; requests left in `Classifying` are routed again. Re-upload a retained recording with `ingest_audio.py`.

Typed text is treated as English when it is plain ASCII (the bedside keyboard is QWERTY); otherwise it is treated as the screen's language.

## Validation

`python -m unittest discover -s hospital-dashboard -p 'test_*.py'`. The tests use stand-ins for Whisper, NLLB and Laya; real models must be checked on the server. Measure end-to-end latency on the M5 with several bedside units speaking at once, and check routing on representative requests in every language you expect. Do not infer clinical accuracy from a working demo: the routing model's urgency head cannot express 8–10, and translation errors can hide clinical meaning.

Only one backend may use a data directory at a time; on Linux and macOS a process lock enforces this. The schema migration only adds columns; nothing existing is deleted. Back up `transcript-data/` before upgrading.
