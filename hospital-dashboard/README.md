# Bello LAN server

One server on the hospital network (an M5 Mac with 24 GB) serves every bedside unit:

1. A bedside Pi records the patient and uploads the audio.
2. faster-whisper transcribes it and detects the language. The Pi shows the patient their own words as soon as the transcript exists.
3. Requests that are not in English are translated to English (NLLB-200, or Whisper large-v3 itself; see `BELLO_EN_ENGINE`).
4. The fine-tuned Laya routing model ([../decision-model](../decision-model/README.md)) suggests a category, department and urgency.
5. The clerk dashboard (`staff-app/clerk.py`) shows the request live. One click, **Send + call**, puts it in that department's inbox (`staff-app/inbox.py`) and logs the department call. The call is a placeholder for now; extensions are in `departments.json`.
6. Staff hold **Staff: hold when done** on the bedside screen when they reach the room. That resolves every open request for the bed and stops the response-time clock.
7. Records sync to the AWS archive ([../aws-backend](../aws-backend/README.md)). The admin dashboard (`staff-app/admin.py`) reads the archive to show the most common issues and response times.

If translation or routing is unavailable, the request still reaches the clerk, flagged for review. It is never held back.

The server is API only; the staff apps in [../staff-app](../staff-app/README.md) and the bedside units in [../patient-app](../patient-app/README.md) are its clients. See [TRANSCRIPT_SETUP.md](TRANSCRIPT_SETUP.md) for installation, configuration, the API and testing.
