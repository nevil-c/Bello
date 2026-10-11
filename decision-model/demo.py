#!/usr/bin/env python3
"""Hand-written requests through a checkpoint, graded against my own answers.

  python demo.py                                  # fine-tuned checkpoint
  python demo.py --model convaiinnovations/laya   # base, for comparison

"gold" is my own reading of what the decision should be, anchored on training
rows where one exists -- not a held-out label set.
"""
import argparse
import json
from build_dataset import QUESTIONS, state
from laya import load

# (context, request, gold)  gold = category / department / urgency / more_info_needed
REQUESTS = [
    ("", "Can someone help me to the toilet?",
     ("Toileting", "Nurse", 4, False)),
    ("", "My room is freezing and the bathroom floor is wet.",
     ("Room Environment / Cleaning", "Housekeeping & facilities", 3, False)),
    ("Patient is NPO and has a swallow evaluation in the morning",
     "I'm hungry, can I see a menu?",
     ("Dietary / Refreshment", "Nurse", 3, False)),
    ("", "The TV remote isn't changing channels.",
     ("Tech Support / Amenities", "Unit Clerk / IT Maintenance", 1, False)),
    ("", "The AC is broken and it's hot in here.",
     ("Room Environment / Cleaning", "Unit Clerk / IT Maintenance", 2, False)),
    ("", "My surgical incision is throbbing and I feel dizzy.",
     ("Urgent Clinical / Pain", "Nurse", 8, False)),
    ("Patient is at high risk of heart attack",
     "I feel extremely cold all of a sudden.",
     ("Urgent Clinical / Pain", "Nurse", 7, False)),
    ("", "You forgot to give me my medicine.",
     ("Urgent Clinical / Pain", "Nurse", 5, False)),
    ("", "Can I get some ice water and apple juice?",
     ("Dietary / Refreshment", "food services", 2, False)),
    ("", "When is my son visiting? Nobody told me the schedule.",
     ("Unknown", "Nurse", 2, True)),
    ("Patient is post-op day 1, vitals stable, on 15-minute checks", "",
     ("Unknown", "Nurse", 2, True)),  # nothing to act on: expect more_info
]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default="outputs/laya-patient")
    p.add_argument("--device", default="mps")
    a = p.parse_args()

    agent = load(a.model, device=a.device)
    states = [state({"patient_context": c, "patient_request": r}) for c, r, _ in REQUESTS]
    results = agent.predict_batch(states, QUESTIONS)
    print(f"model: {a.model}\n")

    hits = {q: 0 for q in QUESTIONS}
    for i, ((ctx, req, gold), res) in enumerate(zip(REQUESTS, results), 1):
        ans = res["answers"]
        got = (
            ans["category"]["choice"],
            ans["department"]["choice"],
            int(max(range(len(ans["urgency"]["probabilities"])),
                    key=lambda j: ans["urgency"]["probabilities"][str(j)])) + 1,
            ans["more_info_needed"]["noul"] > 0.5,
        )
        print(f"{i:2}. {('[' + ctx + '] ') if ctx else ''}{req or '(blank request)'}")
        for qid, g, v in zip(QUESTIONS, gold, got):
            mark = "ok  " if str(g) == str(v) else "MISS"
            hits[qid] += str(g) == str(v)
            extra = ""
            if qid == "urgency":
                extra = f"(mean {ans['urgency']['score'] + 1:.1f}, conf {ans['urgency']['answer_confidence']:.2f})"
            elif QUESTIONS[qid]["type"] == "noul":
                extra = f"P(yes)={ans[qid]['noul']:.2f}"
            else:
                extra = f"p={ans[qid]['probabilities'][ans[qid]['choice']]:.2f}"
            print(f"      {qid:17} {str(v):32} {mark} gold {str(g):28} {extra}")
        print()
    n = len(REQUESTS)
    print("agreement vs my gold:",
          "  ".join(f"{q} {hits[q]}/{n}" for q in QUESTIONS))
    print("json:", json.dumps(results[0]["answers"], ensure_ascii=False)[:200] + " ...")


if __name__ == "__main__":
    main()
