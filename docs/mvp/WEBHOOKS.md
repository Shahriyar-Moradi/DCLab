# DCLab webhooks (P8.8-A)

DCLab can tell your systems when something needs attention or finished. Owners and administrators
add up to 5 endpoints per workspace (`POST /v1/workspaces/{workspace_id}/webhooks`, signed-in
session only) and choose the events: `decision_needed`, `run_finished`, `run_failed`,
`scheduled_scoring_result`, `drift_alert`, `quota_warning` (plus `ping` from the test button).

**What is sent.** `POST` with a JSON body of ids, closed codes, the project id and name, and a link:
never data values, rows, file names or final-test numbers.

```json
{"id": "<delivery id>", "event": "run_failed", "workspace_id": "...", "created_at": "...",
 "data": {"project_id": "...", "project_name": "...", "subject": {"kind": "experiment", "id": "..."},
          "code": "training_failed", "occurred_at": "..."}, "link": "https://<studio>/projects/.../experiments"}
```

**Headers.** `X-DCLab-Event` (the event), `X-DCLab-Delivery` (the delivery id; the same on every
retry), `X-DCLab-Timestamp` (Unix seconds), `X-DCLab-Signature` (`v1=` + hex HMAC-SHA256 of
`"{timestamp}.{raw body}"` with your endpoint's secret), `User-Agent: DCLab-Webhooks/1`.

**Secret.** Shown once, when you add the endpoint or rotate it (`POST …/{webhook_id}/rotate-secret`;
the old secret stops verifying at once). DCLab stores no secret: it derives it from a server key.

**Verify every request.** Reject a signature that does not match and a timestamp more than
300 seconds (5 minutes) from your clock (the replay window). Also drop a request whose
`X-DCLab-Delivery` id you already handled within that window (retries reuse the id):

```python
import hashlib
import hmac
import time

REPLAY_WINDOW_SECONDS = 300


def verify(secret: str, headers: dict, body: bytes, now: float | None = None) -> bool:
    timestamp = headers.get("X-DCLab-Timestamp", "")
    signature = headers.get("X-DCLab-Signature", "")
    if not (timestamp.isascii() and timestamp.isdigit()):
        return False
    if abs((time.time() if now is None else now) - int(timestamp)) > REPLAY_WINDOW_SECONDS:
        return False
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(b"v1=" + digest.encode(), signature.encode())
```

**Delivery rules.** HTTPS only, port 443 unless the platform allows others, no user name or password
in the URL. Private, local and cloud-metadata destinations are refused when the endpoint is added
and again at every delivery (the address DCLab checked is the one it connects to). Redirects are not
followed. At most 5 s per attempt (3 s each for the lookup and the connection; a slower answer is a
failure), at most 4 KiB of your response read and none of it kept. Any 2xx is success. Anything else is retried after 1 min, 5 min, 30 min
and 6 h, then given up; a `ping` is tried once (at most 6 tests per endpoint per hour). After 5 failed deliveries in a row the endpoint is
paused for an hour (its deliveries wait, no attempt is used), then one delivery is tried again;
turning the endpoint off and on clears the pause. At most 60 deliveries per minute per workspace.
The delivery log (`GET …/{webhook_id}/deliveries`) shows each attempt's status code, a closed error
code and the duration; every add, URL / events change, on / off and rotation is recorded with who
did it (the URL's host only). Nothing older than 24 hours is ever sent later.

**For operators.** `WEBHOOK_SECRET_KEY` (at least 32 characters; without it webhooks cannot be added
or sent), `WEBHOOK_ALLOWED_PORTS` (default `443`), `WEBHOOK_MAX_DELIVERIES_PER_MINUTE`,
`PUBLIC_BASE_URL` (links), `NOTIFICATIONS_ENABLED` (kill switch; read when a process starts, so
turning it off takes effect only after the API and worker restart). Email: `EMAIL_PROVIDER` =
`none` (default, nothing sent) | `smtp` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`;
STARTTLS required) | `postmark` (`POSTMARK_SERVER_TOKEN`), with `EMAIL_FROM_ADDRESS`; email goes
only to people whose address is verified (`users.email_verified_at`; demo and seed users may need it
set to receive email locally), at most 10 per person per workspace and 20 per person per day; emails
name no project or workspace (fixed text and a link only). Credentials live in the environment
only. Worker fairness (code constants): one tick spends about 10 s at most and serves workspaces in
turn, each for at most 3 s or 5 rows, and at most 5 rows per endpoint.

**Key blast radius and incident procedure.** Every endpoint's secret is derived from
`WEBHOOK_SECRET_KEY` (key id `k1`, stored per endpoint as `secret_key_id`). Anyone holding that key
and the database ids can sign as DCLab for every endpoint of every workspace. If it leaks: set a new
`WEBHOOK_SECRET_KEY`, restart the API and worker (every endpoint's secret changes at once; deliveries
fail signature checks at the customer until they rotate), then ask each workspace's owners to rotate
their endpoints' secrets and update their receivers. A key map (`WEBHOOK_SECRET_KEYS` with a current
key id, for gradual rotation) is a follow-up; endpoints already record their key id.

**Not yet.** Adding an existing user to a workspace (`workspace_service.add_workspace_member`) has
no consent step, so that person starts receiving the workspace's emails; once an acceptance flow
exists, email should go only to accepted memberships. Platform staff cannot turn off a customer's
endpoint except in the database
(`UPDATE webhook_endpoints SET enabled = false`); the Settings screens (Notifications, Integrations)
and the `/unsubscribe` page are a later step (emails carry no unsubscribe link until it exists);
retention is not addressed: sent and failed outbox rows, the delivery log and the change trail are
kept, and removing webhook rows that have deliveries or changes needs an operator (append-only).
