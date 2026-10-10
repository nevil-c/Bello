# Label-check prompt (hand this to a second model)

**What this is.** A prompt that gives a different model the dataset **as it is** and asks it to check
the existing labels and the sentence quality. It does not write data, does not rewrite rows, and
does not produce a corrected file - the output is a findings list that a human decides on.

**Why a second model.** Every label in ids 68-1067 was assigned and then audited by the same model
(`deepseek-v4-flash`), so both passes share blind spots. A different model family is the cheapest
independent check available.

**One caveat, stated up front.** The checker *can* see the labels, which is what you asked for, and
that does create anchoring - a model that can read "urgency 3" tends to nod at it. The prompt pushes
back in three ways: it must decide each label from the sentence before comparing, it may only report
disagreements (never restate an agreement, or the reply becomes an expensive rubber stamp), and it
must give a reason per finding so a human can judge it. If you ever want the stronger test, strip
the five label columns and score the model's fresh answers against the file - but that produces
labels, which is not what you want here.

## How to run it

1. Chunk the file at ~100 rows per call (`data/patient_requests_all.csv` has 1067, so ~11 calls).
   For a quick read, audit a stratified sample: ~20 rows from each category, ~120 rows total.
2. Paste the prompt below, then attach the chunk as CSV **with all columns**.
3. Save the replies as `audit/findings_001.csv`, `findings_002.csv`, and concatenate them.
4. Read the findings, decide on each one yourself, and only then edit the dataset. Nothing the
   checker returns is applied automatically.

## The prompt (copy everything from here to the end)

You are checking a synthetic dataset of hospital patient requests before it is used to train a
routing model. You are a reviewer, not an author: your job is to find what is wrong with what is
already there. Do not write new requests, do not rewrite the patient's words, do not produce a
corrected dataset.

### What you receive

A CSV with these columns:

- `request_id`
- `patient_context` - a fact from the patient's chart, or empty: a diagnosis, a precaution, a
  procedure, a device, a diet order, or a lasting functional limitation (poor grip, hard of
  hearing, cannot bend). It should never be a live observation ("is not breathing"), a care
  directive ("needs the light kept out"), a future expectation, or general medical knowledge. Context
  is what makes the *same* sentence route or escalate differently, so weigh it heavily.
- `patient_request` - what the patient said out loud. This system replaces the call bell: the patient
  speaks; there are no buttons, screens or typing. A request that mentions pressing a button,
  pulling a cord or using a buzzer is a defect.
- `category`, `destination department`, `urgency`, `clinical_review`, `more_info_needed` - the
  existing labels you are checking.

Real speech contains disfluency, hedges and unfinished sentences. None of that is a defect.

### How to check a row

For each row, decide from the sentence and the chart fact what the labels *should* be - then compare
with what the file says. Do not read the existing label first and look for a reason to agree with
it. Do not report rows where you agree; agreement is silence, not a finding. Only disagreements and
defects go in your output.

### The rules you are checking against

**Category** is one of:

- `Dietary / Refreshment` - food and drink: meals, trays, tea rounds, thirst, menu questions.
- `Toileting` - bedpans, commodes, urinals, pads, toilet paper, bathroom cleanliness, getting to the
  toilet.
- `Room Environment / Cleaning` - the room: cleaning, bedding, temperature, curtains, spills.
- `Tech Support / Amenities` - the television, the phone, the bed's electric controls, a socket, a
  fan, belongings and clothing.
- `Urgent Clinical / Pain` - a clinical problem stated as the request: pain, bleeding, breathing, a
  wound, a line, a device the patient's body is reacting to.
- `Unknown` - no routable ask: calling out with no words, a rant that names nothing, blank or
  garbled speech, a sentence aimed at the wrong person.

**Destination department** is one of `food services`, `Nurse`, `Housekeeping & facilities`,
`Unit Clerk / IT Maintenance`, with these overrides, which are the whole point of the dataset:

- `food services` - ordinary food and drink.
- `Housekeeping & facilities` - cleaning, bedding, temperature, curtains, spills, supplies (toilet
  paper, pads, wipes).
- `Unit Clerk / IT Maintenance` - fixtures and equipment faults: lights, sockets, the bed mechanism,
  the phone, the television, a leaking tap.
- `Nurse` - anything a clinician must act on, and every request governed by a clinical rule.

Overrides that beat the category:

- A **clinical rule beats the category**: a diet order, an NPO ("nothing by mouth") question, an
  allergy question, a swallowing or choking problem, insulin/meal-timing, a protein restriction or
  low-sugar order, a pre-op fasting instruction - all route to `Nurse` even though they sound like
  catering. If a patient asks *whether they may eat*, that is clinical, not a menu question.
- A **device or line problem** - catheter tugging, an IV or oxygen line caught on a rail, a drain, a
  dressing that has come loose - routes to `Nurse`, not to maintenance, even when the words describe
  an object.
- An **accident or incontinence** (wet or soiled bed, briefs, "help cleaning up" from a patient who
  cannot manage alone) routes to `Nurse`; a plain supply request for a bedpan, pad or toilet paper
  routes to `Housekeeping & facilities`.
- **Fall risk** routes to `Nurse`: a patient on fall precautions, or one who cannot walk unaided,
  saying they are getting up, standing, or going to the toilet alone.
- Everything in `Urgent Clinical / Pain` and in `Unknown` goes to `Nurse`.

**Urgency, 1 to 10**, judged from the content, not the tone:

- **1** no impact if it waits - a crooked picture, a stopped wall clock, a question asked out of
  curiosity.
- **2-3** minor, handle on the next round - a cold drink, more toilet paper, an extra pillow, a
  routine question.
- **4-5** needs action soon, someone is waiting, uncomfortable or blocked - waited over an hour, a
  meal that never came, a bathroom that cannot be used, a wet floor, nothing to drink since the
  trolley.
- **6-7** significant: distressed, desperate, or at risk if it waits - "I can't hold on any longer",
  no food or drink all day, a fall-risk patient trying to get up unaided, a blocked toilet with an
  urgent need.
- **8-9** serious, needs attention now - heavy bleeding, an IV line out, severe pain, a new weakness
  or numbness, a fever with a device in place.
- **10** immediate threat where speaking is barely possible - crushing chest pain, cannot breathe,
  choking, unresponsive. A fluent full sentence is rarely a 10.

Rules that override a first impression - flag any row that breaks them:

- **Tone is not severity.** An angry or loud sentence about something minor is still minor; a calm,
  apologetic sentence about something serious is still serious. "Sorry to make a fuss, I can't stop
  shivering" is not a 3. "I want my tea this instant" is not urgent. Urgency that tracks the
  loudness of the words rather than the content is a defect.
- **A quantified report is a finding.** "10 out of 10 pain" with nothing in the chart to soften it is
  at least a 7 and `clinical_review` is TRUE.
- **Context moves urgency in both directions.** The same sentence is worse for a patient with a
  clotting disorder, a transplant, neutropenia after chemotherapy or a fall-risk flag, and milder for
  a patient with no relevant history.
- A request that sounds dramatic but names nothing clinical and has no chart context is a comfort
  request: low urgency, however it is phrased.

**`clinical_review`** should be TRUE when a clinician must see the request itself, regardless of
which department handles it: a symptom, a device or line problem, an accident, bleeding, a fall risk,
a swallowing problem, an allergy question, an NPO or diet-order question. FALSE for a plain comfort,
supply, housekeeping or equipment request. Both directions are defects: TRUE on a plain request, and
FALSE on something a clinician must see.

**`more_info_needed`** should be TRUE when the dashboard operator must **ring the room** because the
request has no actionable ask or names no target ("something is wrong...", "that thing keeps going
off", blank or garbled speech). FALSE for anything dispatchable, however vague the wording - if there
is a clear target, someone can be sent without asking first. A clinically urgent request is never
"ring first": if the patient needs a nurse now, this is FALSE.

### Also check the sentences themselves

Report, in the same findings list, any row where:

- the context is a live observation, a care directive, a future expectation, or general knowledge
  rather than a filed chart fact;
- the request mentions a button, cord, buzzer or screen (this system is voice only);
- the request is unnatural, garbled, or reads like a template rather than something a person would
  say;
- two rows carry substantially the same request with no chart fact to justify the repeat;
- the request and the context contradict each other - for example a context that would make the
  request impossible, or a context that plainly does not explain the label.

### What to output

A CSV findings list only, no prose, one line per problem:

```
request_id,field,given,should_be,why
```

- `field` is one of `category`, `destination department`, `urgency`, `clinical_review`,
  `more_info_needed`, `patient_request`, `patient_context`.
- `given` is what the file says now; `should_be` is your correction. For a sentence-quality problem
  leave `should_be` empty.
- `why` is one short clause.
- One line per field you disagree with. A row with two wrong labels gets two lines.
- If you disagree with a label only because of a chart fact you cannot see, do not report it.

Append at the very end, as comment lines starting with `#`, exactly these three lines:

```
# rows_checked: <n>
# rows_flagged: <n>
# fields_flagged: category <n>, destination department <n>, urgency <n>, clinical_review <n>, more_info_needed <n>, text <n>
```

Flag what is wrong; do not fix the dataset. Do not output rows you agree with. If a chunk is clean,
your entire reply is those three comment lines.
