#!/usr/bin/env python3
"""Patient-request CSV -> Laya fine-tuning rows ({state, questions, expected}).

  python build_dataset.py data/patient_requests.csv --dry-run   # self-check
  python build_dataset.py data/patient_requests.csv             # train/eval JSONL

The same files feed `laya-train --data/--eval` and `laya-evals run`.
"""
import argparse
import csv
import json
import os
import random

CONTEXT, REQUEST = "patient_context", "patient_request"

# Label -> description. Choice option order is positional, so these dicts also
# fix the order the options are shown in; a label the CSV uses but this dict
# lacks is a hard error (a typo would silently drop the row).
CATEGORY = {
    "Toileting": "help getting to the bathroom or with toileting.",
    "Room Environment / Cleaning": "room temperature, bed, cleaning, noise, TV, room setup.",
    "Dietary / Refreshment": "food, drinks, meals or menus.",
    "Tech Support / Amenities": "phones, wifi, devices or other amenities.",
    "Urgent Clinical / Pain": "symptoms, pain, medication or any clinical concern.",
    "Unknown": "the request does not fit any other category.",
}
DEPARTMENT = {
    "Nurse": "clinical and patient-care staff.",
    "Housekeeping & facilities": "cleaning and the physical room.",
    "food services": "the kitchen and meal delivery.",
    "Unit Clerk / IT Maintenance": "ward administration, IT or equipment repair.",
}
# urgency 1-10 in the CSV maps to score level index 0-9 (expected is a level
# index; see laya.train.target_from_expected).
URGENCY = [
    "no time pressure; can wait indefinitely.",
    "routine; handle in the normal round.",
    "routine, but should not be forgotten.",
    "soon; the patient is uncomfortable.",
    "should be handled within the hour.",
    "significant discomfort; handle promptly.",
    "the patient is distressed or may deteriorate.",
    "needs attention within minutes.",
    "emergency; needs an urgent clinical response.",
    "immediate danger; drop everything else.",
]

QUESTIONS = {
    "category": {
        "type": "choice",
        "instructions": "Which category does this request fall into?",
        "criteria": CATEGORY,
    },
    "department": {
        "type": "choice",
        "instructions": "Which department should handle this request?",
        "criteria": DEPARTMENT,
    },
    "urgency": {
        "type": "score",
        "instructions": "How urgent is this request?",
        "criteria": URGENCY,
    },
    "more_info_needed": {
        "type": "noul",
        "instructions": "Does this request need more information before it can be handled?",
    },
}


def state(row):
    """The two input columns as labelled lines; a blank one contributes nothing."""
    lines = []
    for col in (CONTEXT, REQUEST):
        v = (row.get(col) or "").strip()
        if v:
            lines.append(f"{col.replace('_', ' ').capitalize()}: {v}")
    return "\n".join(lines)


def expected(row):
    """The five decisions for one row, in the form laya.train accepts."""
    def flag(col):
        v = (row[col] or "").strip().upper()
        if v not in ("TRUE", "FALSE"):
            raise SystemExit(f"{col} must be TRUE/FALSE, got {v!r}")
        return v == "TRUE"

    try:
        level = int((row["urgency"] or "").strip())
    except ValueError:
        raise SystemExit(f"urgency must be an integer, got {row['urgency']!r}")
    if not 1 <= level <= len(URGENCY):
        raise SystemExit(f"urgency {level} outside 1..{len(URGENCY)}")
    return {
        "category": (row["category"] or "").strip(),
        "department": (row["destination department"] or "").strip(),
        "urgency": level - 1,
        "more_info_needed": flag("more_info_needed"),
    }


def rows(path):
    with open(path, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def build(raw):
    """Rows with a state, checked against the criteria the questions declare."""
    out, dropped = [], 0
    for r in raw:
        st = state(r)
        if not st.strip():
            dropped += 1
            continue
        exp = expected(r)
        for qid in ("category", "department"):
            if exp[qid] not in QUESTIONS[qid]["criteria"]:
                raise SystemExit(
                    f"{qid} value {exp[qid]!r} is not in the criteria dict; "
                    f"known: {sorted(QUESTIONS[qid]['criteria'])}"
                )
        out.append({"state": st, "questions": QUESTIONS, "expected": exp})
    return out, dropped


def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv")
    p.add_argument("--out", default="data")
    p.add_argument("--eval-frac", type=float, default=0.1)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--limit", type=int, help="use only the first N rows (smoke runs)")
    p.add_argument("--dry-run", action="store_true")
    a = p.parse_args()

    raw = rows(a.csv)
    if a.limit:
        raw = raw[: a.limit]
    data, dropped = build(raw)

    # the check: the CSV -> rows path is what silently breaks
    assert data, "no usable rows"
    assert all(d["state"].strip() not in ("nan", "None") for d in data), "blank leaked into state"
    assert all(len(d["state"]) > 0 for d in data)
    blanks = [d for d in data if "\n" not in d["state"]]
    assert blanks, "no single-line state; blank-context rows should still appear"
    assert all(
        list(d["expected"]) == list(QUESTIONS) for d in data
    ), "expected keys drifted from the question ids"
    assert all(isinstance(d["expected"]["urgency"], int) for d in data)
    print(f"{len(data)} rows ({dropped} dropped: no input)\n\n{json.dumps(data[0], indent=2, ensure_ascii=False)}")
    if a.dry_run:
        return

    random.Random(a.seed).shuffle(data)
    n_eval = max(1, round(len(data) * a.eval_frac))
    os.makedirs(a.out, exist_ok=True)
    for name, part in (("eval", data[:n_eval]), ("train", data[n_eval:])):
        path = f"{a.out}/{name}.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for d in part:
                f.write(json.dumps(d, ensure_ascii=False) + "\n")
        print(f"{len(part):5} rows -> {path}")


if __name__ == "__main__":
    main()
