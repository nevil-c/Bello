# Bello patient bell

The patient side of the call bell: one CALL button, a spoken prompt, and the patient's words shown on screen.

Run `python nurse_call.py` (Windows, Python 3 standard library only). Use `python nurse_call.py --demo` to pretend the patient spoke, without a microphone.

Flow: CALL → "I'm listening. Tell me what you need." → patient speaks, words appear live → "Thank you. Your nurse has your message." The only button is ✕ Cancel. If nothing is heard it asks once more.

Accessibility (top-right corner): Keyboard (on-screen keyboard instead of speaking), Colour filter (Standard / High contrast / Night), Larger text.

Keys: F11 full screen, Esc closes the menu or leaves full screen, Space/Enter presses CALL.

Voice and speech recognition use Windows' built-in System.Speech, so nothing needs installing; accuracy is limited. The message is not yet sent anywhere: connecting it to the hospital dashboard backend is the next step. No real patient data is included.
