# AI Call Bell Router (Ops Unleashed Hackathon, Challenge 2: Inpatient)

Bedside touchscreen kiosk where hospital patients type or speak a request. A fine-tuned
classifier routes it to the right department, so non-clinical requests (water, pillows,
TV) skip the nurse and urgent clinical requests reach the nurse immediately.

Full challenge brief: `docs/challenge-brief.docx`. Likely mentor questions: `docs/mentor-questions.md`.

## Judging criteria (from the brief, each scored 1-10)

1. Safety & Accuracy: urgent clinical requests must be isolated from hospitality tasks.
2. Web App Execution: live, responsive patient interface plus a departmental routing log.
3. Operational Impact: lower floor noise, less alarm fatigue, better throughput.
4. Live Demo: patient requests an extra pillow on the tablet, it appears live on the
   housekeeping queue, and the nurse's pager never fires.

Brief claim to back up in the pitch: 60-70% of call bell activations are non-clinical.

## Hardware and OS

- Raspberry Pi (64-bit required for PyTorch) with touchscreen and mic/speaker.
- OS plan: Raspberry Pi OS 64-bit with desktop, Chromium in kiosk mode on boot:
  `chromium-browser --kiosk --noerrdialogs --disable-infobars http://localhost:8000`
- Fallback if RAM is tight: Raspberry Pi OS Lite 64-bit + `cage` + Chromium.
- Build the on-screen keyboard into the web UI (large keys) instead of using the OS keyboard.
- Test mic early with `arecord -l`.
- The same web UI must also run in any browser (the brief asks for a web app on tablet/phone).

## Architecture (planned)

1. Patient UI (web, large font, high contrast): preset buttons (Water, Blanket, Bathroom
   help, Pain) + free text + push-to-talk voice. Show transcript and confirm before sending.
2. Speech-to-text: Whisper. Use translate mode to turn non-English speech into English text.
3. Safety layer (deterministic, runs BEFORE the model): red-flag keyword list
   (pain, chest, breath, bleeding, fell, dizzy, can't feel, etc.) always routes to Urgent Clinical.
4. Classifier: fine-tuned Laya (see Model below). Outputs label + probabilities as JSON.
5. Routing rules after the model:
   - Any clinical signal in a mixed request overrides (or split into two tickets).
   - Low confidence (top prob below threshold) or high Nurse probability -> Nurse.
   - Food/drink requests get a diet-order check flag (NPO, fluid restriction, diabetic).
6. Staff dashboards: per-department live queue, acknowledge button, escalation if not
   acknowledged within N minutes, status message back to the patient.
7. Log every decision: input, predicted label, probabilities, which rule fired.
8. Existing call bell keeps working. This system supplements it.

## Categories

Hackathon training set (about 8 classes):
- Urgent Clinical -> Primary nurse, high priority
- Nurse (non-urgent): pain meds, equipment alarms (IV pump beeping), provider questions
- Nurse Aide (CNA/PCT): bathroom help, bedpan, repositioning, bathing, getting up (fall risk)
- Food & Nutrition Services: meals, water, juice, snacks
- Environmental Services (housekeeping): spills, trash, cleaning, linens, pillows, blankets
- Facilities / Engineering: temperature, lights, bed controls, plumbing
- Tech / Amenities (unit clerk / IT): TV, remote, Wi-Fi, phone, charger
- Other / Unclear -> Nurse

Future categories to mention as configurable: Interpreter Services, Case Management /
Social Work, Spiritual Care, Patient Experience / Guest Services, Security.

Tricky cases to include in training and test data:
- "Can I have water?" from an NPO patient
- "Another blanket" because of chills (possible fever/sepsis)
- "Help me to the bathroom" (fall risk, goes to aide)
- "My IV is beeping"
- Vague: "I don't feel right", "can someone come", "help"
- Mixed: "I want a pillow and I can't breathe well"
- Typos ("hlep"), Spanish ("Necesito agua"), off-topic ("what's the wifi password")

## Model: Laya

- Open-weight decision model from Convai Innovations, released around Sept 2026, Apache 2.0.
- 421M params, ModernBERT-large backbone, non-autoregressive (no text decoder). Choice
  mode returns a probability per label; also has score (0-1) and binary modes.
- Loaded via the `laya` Python package (`laya.Agent`), not `transformers`.
- Weak zero-shot (0.362 vs 0.461 majority baseline): MUST fine-tune. Model card:
  "a fast base to specialise, not a zero-shot decision engine."
- Fine-tuning uses the official RLCD recipe. A public example fine-tune ran on an RTX 5090.
  Plan compute for training (cloud GPU or teammate's GPU).
- Calibrate with a temperature refit (reported ECE 0.466 -> 0.081). The confidence
  threshold depends on this.
- CPU: roughly 200-450 ms per request on a laptop, ~1.3 GB peak RAM, 8 GB RAM recommended.
  No published Raspberry Pi numbers: benchmark on the Pi early. Fallback: run Laya on a
  laptop on the same network and keep the Pi as the screen.
- Multilingual checkpoint (322M, 100+ languages) scores poorly (0.227 MASSIVE), so prefer
  Whisper translate -> English model.
- Alternative: Jev (TypeSafe), paid hosted API. Rejected: patient text leaves the network,
  costs money, slower (reported p50 236-276 ms vs ~33 ms on GPU).
- Backup option if Laya is too slow on the Pi: fine-tuned DistilBERT (about 6x smaller).

## Evaluation priorities

- Headline metric: recall on Urgent Clinical (missed emergencies are the failure that matters).
- Confusion matrix across all classes.
- Test set written by different teammates than the training set.
- Before/after fine-tuning accuracy.

## Pitch numbers

Hours-saved formula (compute in Python, cite a source for each input):
calls per patient per day x non-clinical share x minutes per nurse trip x beds x 365.

## Team preferences

- No em dashes in any writing.
- Concise, direct language.
- Use Python to verify any numbers.
