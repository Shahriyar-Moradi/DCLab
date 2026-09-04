# DCLab E2E Verification Runbook

Use only disposable databases whose name contains `verify` or `e2e`. Never
point these commands at `postgresql://localhost:5432/decisionai`.

## Isolated cluster

The verification cluster in this workspace listens on port **55432**:

```bash
pg_ctl -D artifacts/e2e-verification-pgdata -o "-p 55432 -k /tmp" -l artifacts/e2e-verification-postgres.log start
pg_isready -h localhost -p 55432
```

Python: repository `.venv` (CPython 3.12). Node: `apps/web`.

```bash
export DATABASE_URL=postgresql://localhost:55432/decisionai
export MIGRATION_TEST_DATABASE_URL=postgresql://localhost:55432/postgres
export DCLAB_E2E_DATABASE_URL=postgresql://localhost:55432/dclab_e2e_verify
```

## Migrations

```bash
DATABASE_URL=postgresql://localhost:55432/dclab_verify_empty .venv/bin/alembic heads
DATABASE_URL=postgresql://localhost:55432/dclab_verify_empty .venv/bin/alembic upgrade head
DATABASE_URL=postgresql://localhost:55432/dclab_verify_empty .venv/bin/alembic current
DATABASE_URL=postgresql://localhost:55432/dclab_verify_empty .venv/bin/alembic check
```

Legacy upgrade coverage: `apps/api/tests/test_legacy_tenant_lineage_migration.py`.
PostgreSQL append-only trigger coverage:
`apps/api/tests/test_ml_run_events_postgres_enforcement.py`.

## Backend

```bash
DATABASE_URL=postgresql://localhost:55432/decisionai \
MIGRATION_TEST_DATABASE_URL=postgresql://localhost:55432/postgres \
.venv/bin/pytest apps/api/tests -q --tb=line
```

Focused suites used during this verification:

- `apps/api/tests/test_access_control.py`
- `apps/api/tests/test_access_architecture.py`
- `apps/api/tests/test_business_administration.py`
- `apps/api/tests/test_data_model_lineage.py`
- `apps/api/tests/test_pipeline_observability.py`
- `apps/api/tests/test_pipeline_verifier.py`
- `apps/api/tests/test_adaptive_modeling_phase1a.py`
- `apps/api/tests/test_adaptive_modeling_phase1b.py`
- `apps/api/tests/test_adaptive_modeling_phase1_verification.py`
- `apps/api/tests/test_adaptive_modeling_holdout.py`
- `apps/api/tests/test_adaptive_modeling_single_plan.py`
- `apps/api/tests/test_adaptive_modeling_production_e2e.py`
- `apps/api/tests/test_platform_explorer.py`
- `apps/api/tests/test_platform_explorer.py`
- `apps/api/tests/test_ml2_pipeline_integrity.py`

## Frontend

```bash
cd apps/web
npm run lint
npx tsc --noEmit
npm run build
```

## Browser acceptance

```bash
cd apps/web
npm run e2e
```

This recreates `dclab_e2e_verify`, migrates to Alembic head, seeds four fixture
accounts (password `VerificationOnly123!`), boots FastAPI on 8001 and Next.js
on 3001, and writes screenshots under `artifacts/e2e-verification/` (gitignored).

Fixture emails:

- `dclab-admin@verification.invalid`
- `dclab-developer@verification.invalid`
- `business-admin-a@verification.invalid`
- `business-developer-a@verification.invalid`

JWT for both processes in Playwright is `e2e-verification-only-secret`. The Next
middleware must use the same secret as the API or `/admin` and `/business`
redirect to login.

## Live OpenAI

Requires `OPENAI_API_KEY` in the process environment or `.env`. Never print the
key.

```bash
.venv/bin/dclab verify-openai-smoke
```

Safe fields to retain: `provider`, `model`, `status`, `request_duration_ms`,
`evidence_digest`.

## Static checks

```bash
git diff --check
```
