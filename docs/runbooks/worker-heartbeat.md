# DclabWorkerHeartbeatStale — no worker seen

**Symptom.** No `dclab worker run` process is connected to the database, or the freshest one was last
seen more than 120 s ago (`WORKER_HEARTBEAT_STALE_SECONDS`). `/health/ready` shows the `worker` check
`stale` (`no_worker_seen` or `heartbeat_old`); the API keeps serving (`degraded`, not 503).

**How liveness is measured.** Each worker names its database connections `dclab-worker:<random>`
(`PGAPPNAME`); the API reads `pg_stat_activity`. Postgres shows `state_change` of other roles'
sessions as NULL, so with separate API / worker roles the check is `unknown` / `activity_not_visible`
(never a false "stale"). Fix: one role for both, or a narrow SECURITY DEFINER function returning only
(application_name, state_change) of `dclab-worker:%` sessions. Do not grant `pg_read_all_stats` (it
exposes every session's query text). A running job's `ml_jobs.heartbeat_at` also counts.

**Likely causes.**
- Worker container stopped, crash-looping or OOM-killed during training.
- Worker hung in a stage without heartbeats; database connection lost.
- Expected: `ML_JOB_DISPATCHER=thread|inline` (local debugging) runs no worker; silence the alert there.

**Checks.**
1. `docker compose -f docker-compose.base44.yml ps worker` and
   `docker compose -f docker-compose.base44.yml logs --since 30m worker`.
2. psql: `SELECT application_name, state, now() - state_change AS idle FROM pg_stat_activity
   WHERE application_name LIKE 'dclab-worker:%';`
3. Running jobs: `SELECT id, job_type, now() - heartbeat_at FROM ml_jobs WHERE status = 'running';`

**Safe actions.**
- `docker compose -f docker-compose.base44.yml restart worker`. Abandoned running jobs are re-queued
  by the next worker after `ML_JOB_HEARTBEAT_TIMEOUT_SECONDS` (default 300 s).
- Repeated OOM: lower parallel load (one worker per host) and note the job ids.

**Escalation.** Workers die again within 15 minutes of a restart: page the backend on-call.
