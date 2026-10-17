# DCLab runbooks

One page per alert in [`ops/alerts/dclab.rules.yaml`](../../ops/alerts/dclab.rules.yaml) (P8.5-A).
Each page: symptom, likely causes, checks with exact commands, safe actions, escalation.

| Alert | Runbook |
| --- | --- |
| `DclabJobFailureRateHigh` | [job-failure-rate.md](job-failure-rate.md) |
| `DclabQueueAgeHigh` | [queue-age.md](queue-age.md) |
| `DclabWorkerHeartbeatStale` | [worker-heartbeat.md](worker-heartbeat.md) |
| `DclabHttp5xxRateHigh` | [http-5xx.md](http-5xx.md) |
| `DclabReadinessFailing` | [readiness.md](readiness.md) |
| `DclabScheduledRunsMissed` | [scheduled-runs.md](scheduled-runs.md) |

Earlier operational runbooks (no alert rule): [auth-secret-rotation.md](auth-secret-rotation.md),
[session-cleanup.md](session-cleanup.md), [session-mass-revocation.md](session-mass-revocation.md),
[session-theft.md](session-theft.md), [simulation-archive-forward-repair.md](simulation-archive-forward-repair.md),
[workspace-isolation-invalidation.md](workspace-isolation-invalidation.md).

## Shared tools

- Readiness: `curl -s localhost:8001/health/ready | python3 -m json.tool` (no auth, API port only:
  the Studio origin does not proxy `/health/*`; cached `READINESS_CACHE_SECONDS`, default 5; `503`
  only when `database`, `migrations` or `object_storage` fails; a stale worker is `degraded`).
- Operator view (signed-in platform staff, browser session; bearers are refused):
  `GET /v1/operator/overview`, `GET /v1/operator/failed-jobs`, `GET /v1/operator/workspaces`.
  Ids, codes and counts only; never customer rows.
- Logs: `docker compose -f docker-compose.base44.yml logs --since 30m api worker`. Set
  `LOG_FORMAT=json` for one redacted JSON object per line with `request_id`, `job_id`, `trace_id`.
- Telemetry is off unless `OTEL_EXPORTER_OTLP_ENDPOINT` (or `OBSERVABILITY_ENABLED=true`) is set and
  the `[observability]` extra is installed (`pip install -e '.[observability]'`). Without metrics the
  alerts cannot fire: use the readiness and operator reads above.
- Database shell (compose): `docker compose -f docker-compose.base44.yml exec postgres psql -U postgres decisionai`.

## Rules for every incident

- Never copy customer rows, file contents, prompts or emails into tickets or chat; quote ids and codes.
- Restarting `worker` is safe: running jobs are re-queued after `ML_JOB_HEARTBEAT_TIMEOUT_SECONDS`
  (default 300) and retried up to `ML_JOB_MAX_ATTEMPTS` (default 3).
- Do not edit evidence rows or `ml_jobs` by hand; corrections go through the services.
