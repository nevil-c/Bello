# Bello: recording → ElevenLabs → saved transcript → department inbox

No Twilio, telephone calls, or Raspberry Pi is required for the first test.
Python standard library only. Use fictional demonstration recordings.

## Run

1. Revoke the API key pasted in chat. Create a replacement ElevenLabs key with Speech to Text access. Do not put it in any source file.
2. In Terminal, change to this hospital-dashboard folder. Set the replacement key privately:

```sh
read -s 'ELEVENLABS_API_KEY?New ElevenLabs API key: '
export ELEVENLABS_API_KEY
python3 backend.py
```

The read command above is for macOS zsh. backend.py reads the key from its environment, never from the browser. Its terminal prints a separate private BELLO API token for device/API uploads.

3. Open http://127.0.0.1:8766/ . The old standalone dashboard on 8765 does not use the backend. The UI labels seeded examples; they are NOT transcribed patient speech.
4. Make a WAV or MP3 recording of yourself saying a fictional blanket request. No upload control is added to the patient panel.
5. In a second terminal, change to this folder, set BELLO_API_TOKEN to the token printed by backend.py, and run:

```sh
python3 ingest_audio.py 8 /absolute/path/to/demo-request.wav
```

This sends that recording to ElevenLabs (and uses API quota). The returned text is stored in SQLite under transcript-data/ and replaces the sample text for room 203 B in the dashboard. The saved transcript is not automatically sent to any department.

6. Click Send transcript for room 203 B. The recommended department is preselected. Review it, choose one receiving team, then Send stored transcript.
7. Open Department inbox in the worklist toolbar (or http://127.0.0.1:8766/departments). Select the receiving department. The exact saved transcript appears with room and detected language. Any available team member can acknowledge it, with no individual assignment.

## Backend APIs

All /api endpoints require the X-Bello-Token header.

- GET /api/requests: IDs, saved transcripts, languages, processing state.
- POST /api/requests/{id}/audio: raw WAV/MP3 bytes; saves recording, transcribes through ElevenLabs scribe_v2, stores text. Maximum 10 MB.
- POST /api/requests/{id}/dispatch: JSON {"department":"Housekeeping & Facilities"}. Copies the SERVER-STORED transcript to one shared department inbox. Client-supplied transcript text is ignored.
- GET /api/departments/queue?department=...: department deliveries.
- POST /api/deliveries/{id}/acknowledge: team acknowledgment.
- GET /api/requests/{id}/history: processing/delivery history.

The future Pi uploads audio to the same /audio endpoint. IDs 1–10 are seeded from the current dashboard; dynamic patient/request creation and classification are not included. Preferred languages are demo profile data; transcription detects the recording language. Recommendations and urgency are seeded demo labels, not predictions based on new audio. Review routing manually; this backend does not establish that a request was medically classified correctly. Seeded nurse requests cannot be redirected to nonclinical departments.

Duplicate transcription and department delivery are blocked. Failed transcription is marked Retry required and can be resubmitted. No automatic paid retry occurs. If the server stops during transcription, restart marks interrupted work Retry required; a retry might incur another API charge. A successful transcript cannot be replaced through this API, preserving delivery integrity. Data and histories persist in SQLite. Raw audio remains in transcript-data/.

Department transfer here means a REAL persistent insertion into this app's department inbox, not email, SMS, or an external hospital system. Department users view the inbox in the local demo; remote devices need a secured deployment. This local server and shared demo token are not production identity/access controls. Do not use real patient records for this demo. No live ElevenLabs API request was made while developing the code. Tests mock the provider.

Run tests: python3 test_backend.py

Reference: https://elevenlabs.io/docs/api-reference/speech-to-text/convert
