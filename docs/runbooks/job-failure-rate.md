# DclabJobFailureRateHigh — many ML jobs fail

**Symptom.** More than 20% of finished jobs failed over 30 minutes (at least 5 jobs). Users see runs,
scorings or monitoring windows end as failed.

**Likely causes.**
- A bad deploy of engine or handler code (one `job_type` / `handler_key` dominates).
- Object storage unreachable or out of space (uploads and outputs cannot be read or written).
- A dependency missing from the worker image (e.g. the `[boosting]` extra).
- One workspace retrying a broken input (failures concentrated in one `workspace_id`).

**Checks.**
1. Codes and types: `GET /v1/operator/overview` → `failed_jobs_last_24h`, `jobs_last_24h`.
2. Which jobs: `GET /v1/operator/failed-jobs` (job id, type, handler, code, attempts, workspace id).
3. Code `see_logs`: find the job in the worker log by id:
   `docker compose -f docker-compose.base44.yml logs --since 2h worker | grep <job_id>`.
4. Storage: `curl -s localhost:8001/health/ready | python3 -m json.tool` (`object_storage`).
5. Recent deploy: `git log --oneline -5` on the deployed revision.

**Safe actions.**
- Roll back the last worker deploy, then `docker compose -f docker-compose.base44.yml restart worker`
  (the worker has no live reload; restart it after any engine/service change).
- Batch scoring codes (`feature_contract_failed`, `unsupported_model`, …) are input problems: tell
  the workspace owner through the usual support channel; nothing to fix on the platform.

**Escalation.** Still above threshold 30 minutes after a rollback, or failures in every job type:
page the backend on-call with the codes and job ids (never file contents or rows).
