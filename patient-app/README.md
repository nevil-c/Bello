# Patient call UI

Run with the local backend using the instructions in ../hospital-dashboard/TRANSCRIPT_SETUP.md. The launcher no longer asks for a speech-provider key. CALL records microphone audio, then displays Processing your recording until the backend returns the completed original-language transcript. No live partial text is generated. Accessibility supports keyboard entry, colour themes and larger text. Typed requests save directly without STT.

Rate my service collects a 1–5 rating, Yes/Partly/No resolution answer and an optional comment, linked to the saved request. The backend prevents duplicate feedback. The survey is patient initiated after help; there is no department completion signal. The home screen retains feedback for the most recent saved request in this app session only. Demo mode is offline and sends no requests.

Cancel stops capture or polling; an already uploaded recording can continue processing and remain visible in the clerk dashboard. No automatic retry is made. Closing the launcher stops its backend; interrupted jobs can be explicitly retried from retained recordings. The administrative sample dashboard remains separate; backend analytics require explicit --database selection in that server.
