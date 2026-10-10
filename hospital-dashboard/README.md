# Bello transcript delivery

Run `python3 backend.py` and open http://127.0.0.1:8766/ . Follow TRANSCRIPT_SETUP.md to configure the ElevenLabs environment variable and submit a prerecorded demo. Python standard library only.

Flow: recording API → ElevenLabs → saved SQLite transcript → selected shared department inbox. No Twilio. Department inbox is at /departments. Existing dashboard.py is a standalone preview; use backend.py for the connected workflow.

No live provider call has been tested. Tests use mocked ElevenLabs responses. No real patient data or API credentials are included.
