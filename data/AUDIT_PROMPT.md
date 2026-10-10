# Independent audit prompt (for a second model)

**Why this file exists.** The 1000 generated rows (ids 68-1067) were labelled and then audited by
the *same* model that wrote them, so the two passes share blind spots. This is the prompt to hand a
**different** model - ideally a different model family - so the labels get a genuinely independent
read. The first same-model audit still overturned 469 label cells, so don't assume the labels are
clean just because a pass already happened.

**The one rule that matters: the auditor must never see the labels.** Strip all five label columns
before handing rows over. If the model can read the existing labels it anchors on them and mostly
agrees, which looks like a clean audit and proves nothing. Score the auditor against the labels
afterwards, not during.

**Do not tell it the target distribution either.** Saying "there should be 250 rows per department"
is how the first pass got corrupted - the label follows the count instead of the sentence.

## How to run it

1. Make the blind input (labels removed, ids and text kept):

```python
import csv
rows = list(csv.DictReader(open("data/patient_requests_all.csv", newline="", encoding="utf-8")))
with open("blind_all.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["request_id", "patient_context", "patient_request"], lineterminator="\n")
    w.writeheader()
    w.writerows({k: r[k] for k in w.fieldnames} for r in rows)
```

2. Chunk it. ~100 rows per call is reliable. A stratified sample of ~200 rows is enough to estimate
   the error rate; the full 1067 is a real audit and takes ~11 calls.
3. Paste the prompt below, then attach the chunk as CSV.
4. Save each reply as `audit/out_001.csv`, `out_002.csv`, ... concatenate them into `audit/out_all.csv`.
5. Score it:

```python
import csv, sys
A = {r["request_id"]: r for r in csv.DictReader(open("data/patient_requests_all.csv", newline="", encoding="utf-8"))}
B = {r["request_id"]: r for r in csv.DictReader(open("audit/out_all.csv", newline="", encoding="utf-8"))}
FIELDS = {"category": "your_category", "destination department": "your_destination_department",
          "urgency": "your_urgency", "clinical_review": "your_clinical_review",
          "more_info_needed": "your_more_info_needed"}
shared = [i for i in A if i in B]
print("compared %d of %d rows (auditor missed %d)" % (len(shared), len(A), len(A) - len(shared)))
for f, g in FIELDS.items():
    agree = sum(1 for i in shared if A[i][f].strip() == B[i][g].strip())
    print("  %-22s %5.1f%% agree  (%d/%d)" % (f, 100.0 * agree / len(shared), agree, len(shared)))
print("\ndisagreements (dataset label first, auditor second):")
for i in shared:
    diffs = {f: (A[i][f], B[i][g]) for f, g in FIELDS.items() if A[i][f].strip() != B[i][g].strip()}
    if diffs:
        print("  id=%-5s %-50s %s" % (i, A[i]["patient_request"][:50], diffs))
```

Read the output like this: `category` and `destination department` should agree ~90%+ on a good
model. Urgency will disagree more often - treat a 1-point gap as noise and a 3-point gap as a real
question. Every disagreement on `clinical_review` or `more_info_needed` is worth a look, because
those two flags are binary and a wrong one teaches the model the wrong instinct.

## The prompt (copy everything from here to the end)

You are an independent auditor labelling a synthetic dataset of hospital patient requests. You have
not seen the dataset's own labels and you must not try to guess what they are, or what mix of
answers would look reasonable. Label each request on its own merits. Judge the sentence and the
chart fact in front of you.

### What you receive

A CSV with three columns: `request_id`, `patient_context`, `patient_request`.

- `patient_request` is what the patient said out loud. This system replaces the call bell: the
  patient speaks, there are no buttons, no screens and no typing. A request that mentions pressing
  a button, pulling a cord or using a buzzer is a defect - flag it in `reason`.
- `patient_context` is a fact from the patient's chart, or empty: a diagnosis, a precaution, a
  procedure, a device, a diet order, or a lasting functional limitation (poor grip, hard of
  hearing, cannot bend). It is never a live observation ("is not breathing"), never a care
  directive ("needs the light kept out") and never a future expectation. Context is what makes the
  *same* sentence route or escalate differently, so weigh it heavily.
- Language is real speech, so expect disfluency, hedge words and unfinished sentences. That is not
  an error.

### What to output

Exactly one CSV, no prose before or after, one line per row you were given, with these columns:

```
request_id,your_category,your_destination_department,your_urgency,your_clinical_review,your_more_info_needed,confidence,reason
```

`confidence` is `high`, `medium` or `low`. `reason` is one short clause. Never invent rows, never
drop rows, never rewrite the patient's words. If a row is genuinely unlabelable, still give your
best answer and say why in `reason`.

### Category

Pick one:

- `Dietary / Refreshment` - food and drink: meals, trays, tea rounds, thirst, menu questions.
- `Toileting` - bedpans, commodes, urinals, pads, toilet paper, bathroom cleanliness, getting to
  the toilet.
- `Room Environment / Cleaning` - the room: cleaning, bedding, temperature, curtains, spills.
- `Tech Support / Amenities` - the television, the phone, the bed's electric controls, a socket, a
  fan, belongings and clothing.
- `Urgent Clinical / Pain` - a clinical problem stated as the request: pain, bleeding, breathing,
  a wound, a line, a device the patient's body is reacting to.
- `Unknown` - no routable ask: calling out with no words, a rant that names nothing, blank or
  garbled speech, a sentence aimed at the wrong person.

### Destination department

Pick one, using the category as the starting point:

- **`food services`** - ordinary food and drink requests.
- **`Housekeeping & facilities`** - cleaning, bedding, temperature, curtains, spills, supplies
  (toilet paper, pads, wipes).
- **`Unit Clerk / IT Maintenance`** - fixtures and equipment faults: lights, sockets, the bed
  mechanism, the phone, the television, a leaking tap.
- **`Nurse`** - anything a clinician must act on, and every request governed by a clinical rule.

Then apply the overrides, which are the whole point of the dataset:

- A **clinical rule beats the category**: a diet order, an NPO ("nothing by mouth") question, an
  allergy question, a swallowing or choking problem, an insulin/meal-timing question, a
  protein-restriction or low-sugar order, or a pre-op fasting instruction routes to `Nurse` even
  though it sounds like catering. If a patient asks *whether they may eat*, that is a clinical
  question, not a menu question.
- A **device or line problem** - catheter tugging, an IV or oxygen line caught on a rail, a drain,
  a wound dressing that has come loose - routes to `Nurse`, not to maintenance, even when the
  words describe an object.
- An **accident or incontinence** (wet or soiled bed, briefs, "I could use help cleaning up" from a
  patient who cannot manage alone) routes to `Nurse`; a plain supply request for a bedpan, pad or
  toilet paper routes to `Housekeeping & facilities`.
- **Fall risk** routes to `Nurse`: a patient on fall precautions, or one who cannot walk unaided,
  saying they are getting up, standing, or going to the toilet alone.
- Everything in `Urgent Clinical / Pain` and in `Unknown` goes to `Nurse`.

### Urgency, 1 to 10

Judge how fast this needs a human, from the content, not the tone:

- **1** no impact if it waits - a crooked picture, a stopped wall clock, a question asked out of
  curiosity.
- **2-3** minor, handle on the next round - a cold drink, more toilet paper, an extra pillow, a
  routine question.
- **4-5** needs action soon and someone is waiting, uncomfortable, or blocked - waited over an
  hour, a meal that never came, a bathroom that cannot be used, a wet floor, nothing to drink
  since the trolley.
- **6-7** significant, the patient is distressed, desperate or at risk if it waits - "I can't hold
  on any longer", no food or drink all day, a fall-risk patient trying to get up unaided, a
  blocked toilet with an urgent need.
- **8-9** serious, needs attention now - heavy bleeding, an IV line out, severe pain, a new
  weakness or numbness, a fever with a device in place.
- **10** immediate threat, the kind of problem where speaking is barely possible - crushing chest
  pain, cannot breathe, choking, unresponsive. Reserve 10; a fluent full sentence is rarely a 10.

Rules that override a first impression:

- **Tone is not severity.** An angry or loud sentence about something minor is still minor; a calm,
  apologetic sentence about something serious is still serious. "Sorry to make a fuss, I can't
  stop shivering" is not a 3. "I want my tea this instant" is not urgent.
- **A quantified report is a finding.** "10 out of 10 pain" with nothing in the chart to soften it
  is at least a 7, and `clinical_review` is TRUE.
- **Context moves urgency in both directions.** The same sentence is worse for a patient with a
  clotting disorder, a transplant, neutropenia after chemotherapy, or a fall-risk flag on the
  chart, and milder for a patient with no relevant history.
- If a request sounds dramatic but names nothing clinical and has no chart context, it is a comfort
  request: low urgency, however it is phrased.

### The two flags

`clinical_review` - TRUE when a clinician must see the request itself, independent of which
department handles it: a symptom, a device or line problem, an accident, bleeding, a fall risk, a
swallowing problem, an allergy question, an NPO or diet-order question. FALSE for a plain comfort,
supply, housekeeping or equipment request.

`more_info_needed` - TRUE when the dashboard operator must **ring the room** because the request
has no actionable ask, or names no target ("something is wrong...", "that thing keeps going off",
blank or garbled speech). FALSE for anything dispatchable, however vague the wording - if there is
a clear target, someone can be sent without asking first. A clinically urgent request is never
"ring first": if the patient needs a nurse now, `more_info_needed` is FALSE.

### Two habits to avoid

- Do not label the words: label the situation. "Does this pudding have sugar?" is a diabetes
  question if the chart says so, and a menu question if it does not.
- Do not make the dataset look tidy. You are not balancing categories, departments or urgency
  levels. If your honest labels come out lumpy, that is the answer.

Output only the CSV.
