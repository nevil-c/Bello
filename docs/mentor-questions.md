# Likely Mentor Questions

## Safety and accuracy

1. What is your recall on urgent clinical requests? Have a confusion matrix and explain class weighting / thresholds.
2. What happens when the model isn't confident? Default to the nurse.
3. Mixed requests ("blanket, also my chest feels tight")? Clinical signal overrides, or split into two tickets.
4. Is there a rule-based safety layer in front of the model? Red-flag keyword list always goes to nursing.
5. Requests that sound non-clinical but are clinical: water for an NPO patient, blanket because of chills, bathroom help (fall risk), IV pump beeping.
6. Where did training data come from? Synthetic; test set written by different people; includes typos, slang, fragments.
7. Vague input ("I don't feel right")? Goes to the nurse.
8. How does multilingual work? Whisper translate to English, then classify. Expect a live Spanish test.

## Voice and accessibility

9. Speech-to-text in noisy rooms, accents, elderly or sedated patients? Show transcript, confirm before sending.
10. Is the mic always listening? Push-to-talk (roommate privacy, HIPAA).
11. Patients who can't use a touchscreen? Large preset buttons.

## Hardware and execution

12. Brief asks for a web app on tablet/phone. Why a Raspberry Pi? Cost per bed, locked-down kiosk; same UI runs in any browser.
13. Model on the Pi or in the cloud? Latency, RAM, privacy, Wi-Fi dependency.
14. What if the Pi or network goes down? Existing call bell still works; this supplements it.
15. Can the screen survive disinfectant wipes?
16. How do staff get the request and close the loop? Acknowledge, escalate after N minutes, status to patient.
17. Integration with nurse call systems, EHR, room assignments?

## Model choice (Laya)

18. Why fine-tune instead of prompting a hosted LLM? Offline, privacy, latency, cost.
19a. Why Laya over Jev? Local, free, faster; fine-tuned Laya reported 0.766 vs Jev 0.727 on typed decisions.
19b. Why Laya over a plain fine-tuned DistilBERT? Built-in calibration, binary/score modes, fine-tuning recipe.
19c. Does it run on the Pi? Benchmark it. Fallback: laptop on LAN.
19d. How did fine-tuning change accuracy? Zero-shot is weak (0.362); show before/after.
19e. Where did you fine-tune it? Know compute plan and training time.
19f. Is the multilingual version good enough? No (0.227 MASSIVE); use Whisper translate.
19g. Did you calibrate confidence scores? Temperature refit.
19h. Isn't the model brand new? Benchmarks are self-reported; lean on your own test results.
20. Who decided the categories? Is a nurse aide category missing? How easy to add a department?
21. How do you explain a routing decision? Log input, label, probabilities, rule fired.

## Operational impact

22. Source for 60-70%, and hours-saved math with each assumption.
23. Does the nurse still get pinged? Passive log only.
24. Are other departments staffed 24/7 (food at 3 a.m.)?
25. Liability for misrouting? Is it an FDA medical device (SaMD)? Frame as routing non-clinical requests only; uncertain goes to nursing.
26. Pilot success metrics: nurse response time, share of calls reaching nurses, non-clinical wait time, staff satisfaction.

## Live demo traps to rehearse

- "I want a pillow and I can't breathe well"
- "Necesito agua"
- "hlep"
- A whispered request
- "what's the wifi password"

Highest risk: #3, #5, #14.
