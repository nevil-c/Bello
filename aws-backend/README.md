# Bello cloud archive

Long-term storage for patient request records. The LAN server (`hospital-dashboard/backend.py`) keeps running the live system on its own; this API is a copy for history, reporting and backup, and it feeds the admin dashboard (`staff-app/admin.py`, through the LAN server) and Tableau. API Gateway (API key, TLS, throttled) -> Lambda (Python 3.12) -> DynamoDB (KMS encrypted, point-in-time recovery, deletion protected) and an S3 bucket that Athena reads as SQL tables for Tableau.

Stored per request: room, original transcript and its English translation, detected language and screen language, the routing model's category, department and urgency, department deliveries, the stub call, bedside resolution, patient feedback and the event history. Audio recordings are never uploaded or stored in AWS; they stay on the LAN server.

## Deploy

Needs an AWS account, the AWS CLI configured (`aws configure`), and the [SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html).

```sh
cd aws-backend
sam build
sam deploy --guided --stack-name bello-archive
```

Accept the defaults (set `RetentionDays` if records should expire; 0 keeps them). The outputs list `ApiUrl` and `ApiKeyId`. Get the key value:

```sh
aws apigateway get-api-key --api-key <ApiKeyId> --include-value --query value --output text
```

## SQL and Tableau

Each stored request is also written to a private, encrypted S3 bucket as two flat tables, which the stack registers in Athena (database `bello_archive`, workgroup `bello-archive`):

- `requests`: one row per request with `site_id`, `request_id`, `room`, `recommended_department` (the routing model's suggestion), `delivered_department` (what the clerk sent), `language`, `state`, `transcript`, `transcript_en`, `ui_language`, `category`, `urgency` (1-10), `urgency_confidence`, `more_info_needed`, `review`, `created_at`, `delivered_at`, `acknowledged_at`, `call_placed_at`, `resolved_at`, `minutes_to_deliver`, `minutes_to_acknowledge`, `minutes_to_call`, `minutes_to_resolve` (the response time: call to staff marking it done at the bedside), `rating`, `resolution_answer`, `feedback_comment`, `version`, `synced_at`. Timestamps are UTC.
- `events`: one row per history event with `site_id`, `request_id`, `event`, `event_at`.

**Upgrading an existing stack:** run `sam build && sam deploy` again to add the routing, call and resolution columns. Rows written before the upgrade simply have those columns empty; new snapshots of a request replace its row.

Each request is one S3 object per table that is replaced when a newer version arrives, so queries never see duplicates. Example:

```sql
SELECT category, count(*) AS requests, approx_percentile(minutes_to_resolve, 0.5) AS median_minutes, avg(rating) AS avg_rating
FROM bello_archive.requests GROUP BY category ORDER BY requests DESC;
```

**Connect Tableau:** create an IAM user (or use SSO) and attach the managed policy from the `AnalystReadPolicyArn` output. In Tableau choose **Amazon Athena** (install the Athena JDBC driver if prompted), then enter the region, the workgroup `bello-archive`, the database `bello_archive` and that user's access key. Drag in `requests`, and join `events` on `site_id` and `request_id` if you need the timeline. Use a live connection; the data is small and Athena bills per data scanned (queries are capped at 1 GB each).

## Turn it on at the LAN server

Set these before starting `hospital-dashboard/backend.py` (or `run_local.py`). The admin dashboard reads the archive with the same settings:

```sh
export BELLO_CLOUD_URL=<ApiUrl>
export BELLO_CLOUD_API_KEY=<key value>
export BELLO_SITE_ID=unit-4
```

Without `BELLO_CLOUD_URL` nothing leaves the LAN server, and the admin dashboard explains that the archive is not configured. When on, the backend checks every 15 seconds for new or updated requests and uploads them. If the network is down it keeps working and retries with a growing delay (up to 5 minutes), oldest first. Sample requests that were never used, and demo rows from `--seed-demo`, are not sent. Do not commit the key; keep it in your shell or an env file that is ignored by git.

## API

All calls need the `x-api-key` header. `site` is a short name for the unit or device (letters, digits, `-`, `_`).

| Call | Purpose |
|---|---|
| `PUT /sites/{site}/requests/{id}` | Store a full snapshot. An older `version` never overwrites a newer one (returns `unchanged`), so retries are safe. |
| `GET /sites/{site}/requests/{id}` | One record. |
| `GET /sites/{site}/requests?limit=50&cursor=...` | Records in id order, 100 per page at most. Pass `next_cursor` back as `cursor`. |

There is deliberately no delete call. To remove a record use the DynamoDB console or `RetentionDays`.

## Run the tests

```sh
python -m unittest discover -s aws-backend -p 'test_*.py'
python -m unittest discover -s hospital-dashboard -p 'test_aws_sync.py'
```

The tests use an in-memory stand-in for DynamoDB, so they need neither AWS nor boto3.

## Before real patient data

This was built for a hackathon demo with sample data. Real patient information needs more than this code:

- A signed AWS Business Associate Agreement and your organization's HIPAA review. Lambda, API Gateway, DynamoDB, KMS and CloudWatch are HIPAA-eligible services, but eligibility is not compliance.
- One API key lets a device read and write every site. Move to per-site credentials (IAM roles, or Cognito/JWT with the site in the claim) and restrict who can read.
- The server sends rooms and transcripts, which can identify a patient, and they now also sit in S3 and Athena query results (results expire after 30 days). Decide what identifiers belong in the archive, and add a retention and deletion policy; `RetentionDays` covers DynamoDB only, so add an S3 lifecycle rule for `requests/` and `events/` to match.
- Tableau users see every site's rows. Limit who gets the analyst policy, and consider hiding `transcript` and `feedback_comment` from the Tableau data source.
- Add CloudTrail data events for the table, CloudWatch alarms, and a backup and restore test.
