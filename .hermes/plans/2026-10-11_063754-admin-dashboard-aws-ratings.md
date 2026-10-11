# Admin dashboard, AWS archive and patient ratings — plan

**Goal:** the admin analytics window shows hospital data from the AWS archive, patient ratings included.

**Finding first: this is already built and working.** Verified read-only just now, not assumed:

| Check | Result |
| --- | --- |
| stack `bello-archive` | `UPDATE_COMPLETE`, API `https://jp3dw69mec.execute-api.us-east-1.amazonaws.com/prod` |
| deployed Lambda | sha256 `7a2710da…` **==** `aws-backend/src/app.py` — no code drift |
| archive data | `requests/unit-4` 19 objects, `events/unit-4` 19 objects |
| ratings in the archive | DynamoDB `SITE#unit-4 / REQ#000000000019` → `feedback {rating:5, resolution_answer:"yes", "Quick, thank you."}`; S3 analytics row has `rating`/`feedback_comment` |
| admin data path | `admin_stats.records()` → 19 records, 4 with feedback; `stats()` → requests 15, resolved 11, median 0.3 min, **avg_rating 4.25, ratings 4** |
| tests | `hospital-dashboard` 44 OK · `staff-app` 8 OK · `aws-backend` 17 OK |
| launcher | `bello-server.sh` sources `~/.bello-cloud` and opens clerk+inbox+admin |

So: no code to write, no stack to fix, no ratings going astray. The wiring the request assumes is missing
(`backend.py` → `admin_stats` → archive API; `backend.py` → `aws_sync` worker → Lambda → DynamoDB + S3)
is all present on `main` and green.

**Assumption I'm proceeding on:** the request comes from something seen in the running UI or in Tableau,
not from the repo. The three legs below are the only ones a shell check cannot prove.

## Steps (verification, not construction)

### 1. Open the admin window and read the rating tile

```bash
cd ~/Bello && ./bello-server.sh
```

Expected, admin dashboard tile row: Requests **15** · Median response **0.3 min** · 90th percentile **1.6 min** ·
Average rating **4.25** with **4 ratings (1–5)** · Routing model agreed **…%**.

- Tile shows a number → admin dashboard confirmed, done.
- Tile shows `—` or the window reads `⚠ Cloud archive …` → that is the real bug; go to step 4.

### 2. Prove a new rating travels the whole way

Rate a request from the patient app (or `POST /api/requests/<id>/feedback` with
`{"rating":5,"resolution_answer":"yes","comment":"…"}`), wait ≤15 s for `aws_sync.INTERVAL`, then:

```bash
aws dynamodb get-item --table-name bello-archive-RequestsTable-J2120Z4HWY4 \
  --key '{"pk":{"S":"SITE#unit-4"},"sk":{"S":"REQ#0000000000NN"}}' --query Item.feedback
```

Expected: the feedback object with your rating. The feedback event bumps `MAX(history.created)`, which is the
sync's version key, so the resend is automatic — no manual push.

### 3. Confirm the Athena / Tableau leg

Last Athena query ran 02:15; all archive data landed 06:33 — so this leg has **never** been exercised with data in it.

```bash
aws athena start-query-execution --work-group bello-archive \
  --query-string "SELECT category, count(*) AS requests, approx_percentile(minutes_to_resolve,0.5) AS median_minutes, avg(rating) AS avg_rating FROM bello_archive.requests GROUP BY category" \
  --result-configuration OutputLocation=s3://bello-archive-analyticsbucket-hdyxqjevgouh/athena-results/
```

Expected: result rows, `avg_rating` non-null for the categories that have ratings. Glue tables and SerDe are
already correct (`requests` → `s3://…/requests/`, `events` → `s3://…/events/`, JSON SerDe, columns match what
`analytics_rows()` writes).

### 4. Only if step 1 fails

Check the server process actually inherited the cloud config — it silently runs archive-less otherwise:

```bash
ps eww $(pgrep -f hospital-dashboard/backend.py) | tr ' ' '\n' | grep BELLO_CLOUD_URL
```

Empty → `~/.bello-cloud` did not reach the launcher (fix the file or the `bello-server.sh` line 11 source).

### 5. Leave nothing running

Kill the server/relay started for the check.

## Files likely to change

None. Contingent only: `bello-server.sh` (cloud env not reaching the server) or `hospital-dashboard/admin_stats.py`
(if the cache/round-trip is the fault) — both only if step 1 shows a real symptom.

## Risks / open questions

- **What did you actually see?** "Fix the AWS stuff" has no matching broken state on disk: stack green, Lambda
  hash == source, ratings present in S3, DynamoDB and the stats payload, all test suites passing. If you saw a
  specific error (admin window `Cloud archive unreachable`, a 5xx from the API, Tableau refusing to connect), tell
  me the text and I will target that instead of this.
- The archive holds **only demo data** (19 requests, site `unit-4`, from this machine). A real unit archives by
  exporting `BELLO_CLOUD_URL/API_KEY/SITE_ID` there — no code change.
- `origin/add-decision-model` has 4 patient-UI commits not on `main`. Unrelated to this request (patient app, not
  admin/AWS) — flagged only so it does not get lost.
