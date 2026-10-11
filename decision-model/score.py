#!/usr/bin/env python3
"""Score a checkpoint on a {state, questions, expected} JSONL.

  python score.py ~/laya-out/ckpt/laya-patient data/eval.jsonl --json ref.json

`laya-evals` only takes a Router alias or an ONNX path, so a locally fine-tuned
checkpoint has to be scored directly. Metrics match what the sweep compares:
per-question accuracy, urgency MAE (in levels) and within-1, mean confidence.
"""
import argparse
import json
from build_dataset import QUESTIONS
from laya import load


def main():
    p = argparse.ArgumentParser()
    p.add_argument("model")
    p.add_argument("data")
    p.add_argument("--device", default="mps")
    p.add_argument("--batch-size", type=int, default=16)
    p.add_argument("--json")
    a = p.parse_args()

    rows = [json.loads(line) for line in open(a.data, encoding="utf-8") if line.strip()]
    agent = load(a.model, device=a.device)
    results = agent.predict_batch([r["state"] for r in rows], QUESTIONS, batch_size=a.batch_size)

    hits = {q: [0, 0] for q in QUESTIONS}
    urg_errs, confs = [], []
    for row, res in zip(rows, results):
        ans, exp = res["answers"], row["expected"]
        urg = ans["urgency"]["probabilities"]
        got = {
            "category": ans["category"]["choice"],
            "department": ans["department"]["choice"],
            "urgency": int(max(urg, key=urg.get)),  # level index 0-9, same as expected
            "more_info_needed": ans["more_info_needed"]["noul"] > 0.5,
        }
        for q in QUESTIONS:
            hits[q][1] += 1
            hits[q][0] += str(got[q]) == str(exp[q])
        urg_errs.append(abs(got["urgency"] - int(exp["urgency"])))
        confs.append(ans["category"]["probabilities"][ans["category"]["choice"]])

    correct = sum(h[0] for h in hits.values())
    total = sum(h[1] for h in hits.values())
    out = {
        "model": a.model,
        "rows": len(rows),
        "overall_acc": correct / total,
        "overall_correct": correct,
        "overall_n": total,
        "per_question": {q: {"acc": h[0] / h[1], "correct": h[0], "n": h[1]} for q, h in hits.items()},
        "urgency_mae_levels": sum(urg_errs) / len(urg_errs),
        "urgency_within_1": sum(e <= 1 for e in urg_errs) / len(urg_errs),
        "mean_category_conf": sum(confs) / len(confs),
    }
    for q, h in hits.items():
        print(f"  {q:18} {h[0]:4}/{h[1]:<4} {h[0] / h[1]:6.1%}")
    print(f"  {'OVERALL':18} {correct:4}/{total:<4} {correct / total:6.1%}"
          f"   urgency MAE {out['urgency_mae_levels']:.2f}, within-1 {out['urgency_within_1']:.1%}")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)


if __name__ == "__main__":
    main()
