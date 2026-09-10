# S0-P01B truth drift detection

**Status:** CURRENT  
**Plan/prompt:** S0-P01B  
**Alembic head pinned:** `0058_simulation_workspace`  
**Does not rewrite** historical 0027/0053 evidence bodies. Status stays in
[`README.md`](README.md).

CI turns S0-P01A mechanical facts into failing checks. Application ML behavior
is unchanged.

## Commands

```bash
# Read-only: graph, snapshots, SDK, docs links, CURRENT vs HISTORICAL, secrets
.venv/bin/python -m scripts.check_truth_drift

# After alembic upgrade head (CI). Do not run against a stale local database.
.venv/bin/python -m scripts.check_truth_drift --alembic-check

# Intentional contract change only
.venv/bin/python -m scripts.check_truth_drift --write-snapshots

.venv/bin/pytest -q apps/api/tests/test_truth_drift.py
```

Refresh rules and reviewer guidance: [`contracts/README.md`](../../contracts/README.md).

## What each check detects

| Check | Pass on current tree | Synthetic failure |
| --- | --- | --- |
| `alembic_graph` | one head `0058_simulation_workspace`, 58 unique revisions | two heads; duplicate revision id |
| `model_migration_tables` | SQLAlchemy tables match snapshot (includes `auth_sessions`) | add/remove table name |
| `alembic_metadata` | CI after `alembic upgrade head`; also `test_historical_alembic_revisions.py` | injected `add_table` diff |
| `v1_openapi_snapshot` | canonical `/v1` JSON | missing `/v1/me` |
| `openapi_operations` | 157 `METHOD path` rows | remove `POST /v1/execution-requests`; add `/v1/new` |
| `sdk_routes` | every SDK `/v1` template matches OpenAPI | SDK path not in API; API path not in SDK |
| `sdk_types` | SDK DTO fields match OpenAPI components | required field `role` dropped from SDK |
| `truth_baseline` | heads/counts in `contracts/truth_baseline.json` | operation count 999 |
| `docs_links` | tracked markdown local targets exist | `[x](no-such-file.md)` |
| `current_status_docs` | HISTORICAL banners + CURRENT names 0054 | CURRENT doc naming `0027_*` |
| `production_secrets` | default JWT only in allowlisted local files | `DCLAB_ENV=production` + default JWT; secret in `docker-compose.yml` |
| object-store / Playwright | existing gitignore guards | already covered by `test_object_store_git_guard.py` |

## Intentional change vs accidental drift

Accidental drift is a CI failure with no snapshot file in the PR, or a snapshot
refresh without a matching product change.

Intentional change is the product/API/schema patch **plus** a snapshot diff in
the same PR, with the PR stating additive vs breaking. Reviewers read
`contracts/*.json` the way they read an OpenAPI changelog. Do not “fix” CI by
rewriting historical reports.

Known limitation: `apps/web/middleware.ts` still falls back to the development
JWT secret. That is allowlisted here and remains Scope 0.2 (S0-P02) work.
`--alembic-check` against an unmigrated local `decisionai` database is a false
positive; CI migrates first.

## Evidence

```text
Plan/prompt ID: S0-P01B
Claim: Truth-drift checks pass on the current tree and fail on synthetic violations
Status: VERIFIED
Environment: local .venv CPython 3.12.13; pytest does not require --alembic-check
Commands:
  python -m scripts.check_truth_drift
  pytest -q apps/api/tests/test_truth_drift.py
Expected result: CLI all [clean]; 18 pytest passed
Observed result: CLI all [clean]; 18 passed
Artifact: contracts/*.json ; docs/verification/S0_P01B_TRUTH_DRIFT.md
Security and tenant checks: production default JWT rejected when DCLAB_ENV=production; no tenant behavior change
Rollback/kill switch: revert this prompt's scripts, contracts, CI step, and tests
Known limitations: web middleware JWT fallback allowlisted; metadata compare only after migrate
Reviewer/date: S0-P01B / 2026-09-10
```
