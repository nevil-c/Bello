#!/usr/bin/env python3
"""Ask the fine-tuned decision model, interactively.

  python ask.py                                     # REPL
  python ask.py --input "I feel cold"
  python ask.py --context "post-op day 1" --input "I want to sit up"
  python ask.py --model convaiinnovations/laya      # the base checkpoint, to compare

REPL: type a request, Enter to decide. '<context> | <request>' adds context;
a bare request is fine (no context). 'quit' or Ctrl-D exits.

What the numbers on each line mean:
  p 97% of 6 options   how much of the model's probability mass is on the option
                       it printed, out of that question's options. A share, not a
                       probability that the answer is right.
  P(yes) 98%           yes/no questions: the chance the answer is yes.
  conf 26%             calibrated probability that the printed answer is right.
  mean 5.3             urgency: the probability-weighted average level. The level
                       before it is the single most likely one; when the two
                       disagree the model is spread across neighbouring levels.
--verbose dumps the raw probabilities for everything.
"""
import argparse
import json
import os
import sys
from build_dataset import QUESTIONS, state
from laya import load


def show(agent, ctx, req, verbose=False):
    text = state({"patient_context": ctx, "patient_request": req})
    ans = agent.predict_batch([text], QUESTIONS)[0]["answers"]
    cat, dep, urg = ans["category"], ans["department"], ans["urgency"]
    lvl = int(max(urg["probabilities"], key=urg["probabilities"].get)) + 1
    rows = [
        ("category", cat["choice"], f'p {cat["probabilities"][cat["choice"]]:.0%} of {len(cat["probabilities"])} options'),
        ("department", dep["choice"], f'p {dep["probabilities"][dep["choice"]]:.0%} of {len(dep["probabilities"])} options'),
        ("urgency", f'{lvl}/10   mean {urg["score"] + 1:.1f}', f'conf {urg["answer_confidence"]:.0%}   (chance of {lvl}/10)'),
        ("more_info_needed", str(ans["more_info_needed"]["noul"] > 0.5), f'P(yes) {ans["more_info_needed"]["noul"]:.0%}'),
    ]
    print(f"  {ctx or '(no context)'} | {req or '(blank request)'}")
    w = max(len(v) for _, v, _ in rows)  # keep the note column aligned for any label length
    for name, val, note in rows:
        print(f"    {name:17} {val:<{w}} {note}")
    if verbose:
        print(json.dumps(ans, indent=2))
    print()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", default=os.path.expanduser("~/laya-out/ckpt/laya-patient"),
                   help="checkpoint dir (default: the fine-tuned one)")
    p.add_argument("--device", default="mps")
    p.add_argument("--context", default="")
    p.add_argument("--input")
    p.add_argument("--verbose", action="store_true", help="also dump the raw probabilities")
    a = p.parse_args()

    agent = load(a.model, device=a.device)
    if a.input or a.context:
        show(agent, a.context, a.input or "", a.verbose)
        return

    print(f"{a.model} on {a.device}.  '<context> | <request>', or just a request. quit to exit.")
    print("p = share of options, P(yes) = chance yes, conf = chance the answer is right.\n")
    for line in sys.stdin:
        line = line.strip()
        if line in ("quit", "exit"):
            break
        if not line:
            continue
        ctx, sep, req = line.partition("|")
        show(agent, ctx.strip() if sep else "", req.strip() if sep else ctx.strip(), a.verbose)


if __name__ == "__main__":
    main()
