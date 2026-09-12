# Session cleanup

**Prompt:** S0-P02E  
**Owner:** `auth.session_cleanup` / `apps/api/app/services/session_cleanup_service.py`

Expired and revoked `auth_sessions` and consumed/expired `auth_recovery_tokens`
are deleted in bounded `LIMIT` batches with `FOR UPDATE SKIP LOCKED`. Issue and
lookup already run one opportunistic batch. This runbook is for idle
deployments and incident follow-up.

## When

- After a mass-revocation or secret rotation, to shrink retained hashed rows.
- When `SESSION_RETENTION_DAYS` has passed and no login traffic is running cleanup.

## Safe procedure

1. Confirm `AUTH_BROWSER_SESSIONS_ENABLED` is the intended value. Cleanup does
   not re-enable sessions.
2. Queue at most one job (payload is empty; `ml_jobs.workspace_id` is queue
   plumbing and is ignored). From the repository root:

```bash
PYTHONPATH=apps/api .venv/bin/python - <<'PY'
from uuid import UUID
from app.db.models import DEFAULT_WORKSPACE_ID
from app.db.session import get_session_factory
from app.services.session_cleanup_service import enqueue_auth_cleanup

Session = get_session_factory()
with Session() as db:
    job = enqueue_auth_cleanup(db, workspace_id=UUID(str(DEFAULT_WORKSPACE_ID)))
    db.commit()
    print(job.id, job.status, job.handler_key)
PY
```

3. Let `dclab worker run` / `make worker` claim the job, or rely on the
   in-process thread dispatcher if `ML_JOB_DISPATCHER=thread`.
4. Repeat only if a second queued job is needed after the first reaches a
   terminal state. Enqueue is idempotent while a job is still `queued`.

## Bounds

- `SESSION_CLEANUP_BATCH_SIZE` (default 500)
- `SESSION_CLEANUP_MAX_BATCHES` (default 20)

Do not raise these without watching row locks. Do not add Redis for cleanup.

## Audit

Cleanup must not log raw tokens, emails, or session ids. Metrics use reason
codes only (`session.expired` / `session.invalid` on lookup, not on delete).
