# Request routing decision model

[Laya](https://huggingface.co/convaiinnovations/laya) (apache-2.0) fine-tuned on
`data/patient_requests_fixed.csv`. Laya is **not** an LLM: a non-autoregressive ModernBERT-large
encoder plus typed question heads, trained against proper scoring rules. It never generates text -
you pass a state and a dict of typed questions, and get typed answers with probabilities back in one
forward pass.

## What it answers

| question | type | output |
|---|---|---|
| `category` | choice, 6 options | label + probability per option |
| `department` | choice, 4 options | label + probability per option |
| `urgency` | score, 10 levels | level index 0-9, i.e. CSV urgency - 1 |
| `more_info_needed` | noul | P(yes), plus a calibrated answer confidence |

The dataset also carries `clinical_review` (see `../data/README.md`). This model does **not** predict
it - that column and its question were removed, so clinical routing rides on `department`
(`Nurse`) alone. Dropping a question needs no retrain: a checkpoint only answers what it is asked,
and the other four fields are unchanged by it.

## Running it

    python -m venv .venv && .venv/bin/pip install -r requirements.txt

    .venv/bin/python build_dataset.py ../data/patient_requests_fixed.csv   # -> data/train.jsonl + data/eval.jsonl
    .venv/bin/laya-train --data data/train.jsonl --eval data/eval.jsonl \
      --epochs 4 --micro-batch 8 --grad-accum 8 --device cuda --out outputs/laya-patient
    .venv/bin/python score.py outputs/laya-patient data/eval.jsonl         # per-field metrics

`build_dataset.py` takes the CSV path as an argument and reads it from `../data/`, so the canonical
dataset stays in one place. It asserts the mapping on every build (`--dry-run` prints counts only):
a CSV label that is not a criteria key is a hard error, not a silently dropped row. 1064 rows ->
1063 usable -> 957 train / 106 held out.

`ask.py` is an interactive prompt against a checkpoint. `demo.py` runs 11 hand-written requests
against hand-written gold - a sanity check, not a measurement.

A local checkpoint directory has to be scored with `score.py`: `laya-evals run --model` only accepts
Router aliases (`english` / `multilingual` / `typed-decisions`) or an ONNX path.

## Held-out results (106 rows, one seed)

| field | base checkpoint | fine-tuned |
|---|---|---|
| category | 59/106 55.7% | **96/106 90.6%** |
| department | 61/106 57.5% | **90/106 84.9%** |
| urgency | 11/106 10.4% (MAE 3.51) | 32/106 30.2% (MAE 1.17, within 1 level 65.1%) |
| more_info_needed | 96/106 90.6% | 103/106 97.2% |
| overall | 227/424 53.5% | 321/424 75.7% |

Read those against a constant answer, not against zero: always-`Dietary` scores 26% on category and
always-`Nurse` 38% on department, so both carry real signal. `more_info_needed` does not - it is 7%
TRUE in the data, so always-`no` scores 93% and that 97.2% says little.

## Weights

Not in this repo: `.gitignore` excludes `*.safetensors`, `*.pt`, `*.bin` and `checkpoints/`. The
fine-tuned checkpoint is 842 MB fp32 (encoder + heads + tokenizer + the question spec), produced by
the `laya-train` command above. Nothing here can load it until it is hosted somewhere the app can
fetch it.

## Known limits (measured, not guessed)

- **Urgency cannot express 8-10.** The top two levels hold 50 of 1064 rows, so the head puts no mass
  there: a genuine "can't breathe" lands around 7/10. Any top-band triage band has to come from the
  app, not from this head.
- **Urgency is the noisy head.** Two training runs on near-identical data flipped the argmax level on
  59 of 111 held-out rows, so a small urgency delta between runs is not an improvement.
- **`more_info_needed` is prior-bound.** 77 of 1064 rows are TRUE. Recall on the TRUE class is 4/5 at
  a 0.5 cut and 5/5 at 0.2-0.3 - the threshold moves this field more than the weights do.
- **A blank request is invisible.** `state()` omits a blank column, and only 2 of 1064 rows have an
  empty request, so the model has nothing to work with when the patient says nothing. Handle the
  empty case in the app.
- **n = 106 per field.** One row is 0.9 points and single-seed, so per-field moves under ~8 points
  are unproven at this sample size.
