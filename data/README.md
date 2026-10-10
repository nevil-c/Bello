# Patient request dataset

`patient_requests_all.csv` - 1067 rows, one row per patient request. A patient speaks one sentence;
the kiosk routes it to a department; a classifier is trained on the labels.

**Provenance is in the `request_id` range:**

| ids | rows | origin |
|---|---:|---|
| 1-25 | 25 | hand-written by the team (the seed set) |
| 26-67 | 42 | hand-written pilot |
| 68-1067 | 1000 | generated, then every label audited row by row against a written rubric, then rebalanced to the target distribution |

## Columns

| column | meaning |
|---|---|
| `request_id` | stable id; the range tells you the provenance |
| `patient_context` | a **filed chart fact** (diagnosis, precaution, procedure, device, diet order) or empty. Never a live observation, never something a machine is reading right now |
| `patient_request` | what the patient said. Voice only - there are no buttons, no typing, and no call-bell hardware |
| `category` | `Dietary / Refreshment`, `Toileting`, `Room Environment / Cleaning`, `Tech Support / Amenities`, `Urgent Clinical / Pain`, `Unknown` |
| `destination department` | `food services`, `Nurse`, `Housekeeping & facilities`, `Unit Clerk / IT Maintenance` |
| `urgency` | 1-10. 1 = no impact if it waits, 5 = should be handled soon, 7-9 = significant, 10 = immediate threat with speech barely possible |
| `clinical_review` | TRUE when a clinician must see the request itself (a symptom, a device or line problem, an accident, a fall risk) - independent of which department ends up owning it |
| `more_info_needed` | TRUE when the operator must **ring the room** because the request has no actionable ask or names no target. FALSE for anything dispatchable |

Routing: the category decides the department, then `patient_context` decides whether a clinical rule
takes over. A diet order, an allergy question, an NPO permission ask or a catheter problem routes to
`Nurse` even when the words sound like a catering or housekeeping request - that mapping is the point
of the dataset.

## Distribution

The generated 1000 rows (ids 68-1067) hit the target exactly:

- departments: 250 each
- categories: Dietary 260, Toileting 200, Room 170, Tech 160, Urgent Clinical 150, Unknown 60
- urgency 1-10: 96 / 184 / 224 / 158 / 111 / 83 / 61 / 37 / 26 / 20 (50.4% low, 35.2% mid, 14.4% high)
- 247 rows carry a chart fact; 755 of 1000 utterances are distinct

Adding the 67 hand-written rows on top shifts the totals (1067 rows: Nurse 292, Housekeeping 263,
food services 258, Unit Clerk 254). The human rows were kept as authored rather than bent to fit.

## Known quirks in the hand-written rows, kept on purpose

- ids 2, 14, 23 carry label inconsistencies (a non-clinical row flagged `clinical_review TRUE`, a
  missed-dose request at urgency 7 with `clinical_review FALSE`, a clinical row at urgency 9 with
  FALSE). The seed set is treated as read-only history.
- ids 6, 8, 25, 64-67 have a chart fact with no same-text sibling, so the context is not
  load-bearing for those rows.
- 11 mechanical fixes were applied **only in this aggregate**, not in the seed file: 6x category
  `Room Environment/ Cleaning` -> `Room Environment / Cleaning`, 2x `housekeeping & facilities` ->
  `Housekeeping & facilities`, 2x `nurse` -> `Nurse`, and one blank urgency (id 8, a menu request
  from a patient going to chest surgery) filled with 3.

## Checking the labels with a second model

`AUDIT_PROMPT.md` is a self-contained prompt: select the whole file, copy it, paste it into a
different model, then paste the rows you want checked underneath. Nothing to edit first.

- Chunk the file at ~100 rows per paste (`patient_requests_all.csv` has 1067, so ~11 pastes). For a
  quick read, take about 20 rows from each category instead.
- Paste it with **every column**, labels included. The checker is verifying the existing labels, not
  producing new ones.
- Save each reply as a CSV and treat it as a findings list, not gospel: one line per suspected
  problem, with a reason. Nothing is applied automatically - read the finding, decide, then edit.
- Worth using a different model family from the one that wrote the labels, since the same family
  shares its blind spots.
- Showing the checker the labels does cost some independence, which is why the prompt tells it to
  decide each label from the sentence before comparing, and to stay silent about rows it agrees
  with. If a chunk comes back with zero findings, that is a clean chunk - not the checker being
  lazy.
