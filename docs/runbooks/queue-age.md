# DclabQueueAgeHigh — queued ML jobs wait too long

**Symptom.** The oldest queued job has waited more than 10 minutes. Runs stay "queued" in Studio.

**Likely causes.**
- No worker running, or every worker busy with long training jobs (one worker runs one job at a time).
- Workers crash-looping on start (bad image, missing setting, database unreachable).
- `ML_JOB_DISPATCHER` is not `postgres` in the API (jobs queued for an in-process thread that is gone).

**Checks.**
1. `GET /v1/operator/overview` → `queue.depth`, `queue.oldest_age_seconds`, `workers.count`,
   `workers.heartbeat_age_seconds`.
2. Worker containers: `docker compose -f docker-compose.base44.yml ps worker` and
   `docker compose -f docker-compose.base44.yml logs --since 30m worker`.
3. What is running (psql): `SELECT job_type, status, count(*), min(queued_at) FROM ml_jobs
   WHERE status IN ('queued','running') GROUP BY 1, 2;`
4. Dispatcher: `docker compose -f docker-compose.base44.yml exec api env | grep ML_JOB_DISPATCHER`.

**Safe actions.**
- Start or restart workers: `docker compose -f docker-compose.base44.yml up -d worker`
  (scale with `--scale worker=2`; claims use `FOR UPDATE SKIP LOCKED`, so extra workers are safe).
- One job at a time by hand: `docker compose -f docker-compose.base44.yml exec worker dclab worker run --once`.
- Do not delete queued rows; cancel a run through `POST /v1/experiments/{experiment_id}/cancel`.

**Escalation.** Queue still growing with healthy workers for 30 minutes: capacity problem; page the
backend on-call and the founder (more workers or the P8.3 compute target).
