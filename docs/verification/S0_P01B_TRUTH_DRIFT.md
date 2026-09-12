# S0-P01B truth drift detection

**Status:** CURRENT  
**Plan/prompt:** S0-P01B  
**Canonical facts:** [`contracts/truth_baseline.json`](../../contracts/truth_baseline.json)
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
.venv/bin/python -m scripts.generate_truth_artifacts
.venv/bin/python -m scripts.generate_truth_artifacts --check
.venv/bin/python -m scripts.generate_truth_artifacts --verify-idempotent

.venv/bin/pytest -q apps/api/tests/test_truth_drift.py
```

Refresh rules and reviewer guidance: [`contracts/README.md`](../../contracts/README.md).

## What each check detects

| Check | Pass on current tree | Synthetic failure |
| --- | --- | --- |
| `alembic_graph` | live graph matches the canonical baseline | two heads; duplicate revision id |
| `model_migration_tables` | SQLAlchemy tables match snapshot (includes `auth_sessions`) | add/remove table name |
| `alembic_metadata` | CI after `alembic upgrade head`; also `test_historical_alembic_revisions.py` | injected `add_table` diff |
| `v1_openapi_snapshot` | canonical `/v1` JSON | missing `/v1/me` |
| `openapi_operations` | live operation membership matches the canonical artifact | remove `POST /v1/execution-requests`; add `/v1/new` |
| `sdk_routes` | every SDK `/v1` template matches OpenAPI | SDK path not in API; API path not in SDK |
| `sdk_types` | SDK DTO fields match OpenAPI components | required field `role` dropped from SDK |
| `truth_baseline` | aggregate schema/API plus repository/source/test/web inventory in `contracts/truth_baseline.json` | operation count 999 or inventory drift |
| `generated_truth_artifacts` | generator output and manifest equal every checked byte | bounded stale artifact content or missing manifest |
| `docs_links` | tracked Markdown local targets exist; examples in inline/fenced code are ignored | a real link to a missing file |
| `current_status_docs` | HISTORICAL banners + CURRENT canonical head + latest product lineage | CURRENT doc naming a foreign head or stale product SHA; a combined product/evidence commit is accepted only when it is the latest commit for both |
| `production_secrets` | default JWT only in allowlisted local files | `DCLAB_ENV=production` + default JWT; secret in `docker-compose.yml` |
| object-store / Playwright | existing gitignore guards | injected missing ignore rules and tracked private CSV/trace fixtures |

## Intentional change vs accidental drift

Accidental drift is a CI failure with no snapshot file in the PR, or a snapshot
refresh without a matching product change.

Intentional change is the product/API/schema patch **plus** a snapshot diff in
the same PR, with the PR stating additive vs breaking. Reviewers read
`contracts/*.json` the way they read an OpenAPI changelog. Do not “fix” CI by
rewriting historical reports.

`scripts.generate_truth_artifacts` is the only checked-artifact writer.
`scripts.check_truth_drift` is read-only. CI regenerates twice, requires
byte-identical results and a clean `git diff -- contracts/`, and then runs the
real-tree and migrated-metadata detectors without network access.

`--alembic-check` against an unmigrated local `decisionai` database is a false
positive; CI migrates first. Link-shaped strings in Markdown code examples are
not links and are excluded from the link check. Because a document cannot
contain the hash of the commit that contains that document, the lineage guard
accepts a non-embedded product SHA only when the same commit is also the latest
change to the CURRENT truth file; the next product-only commit fails normally.

## Evidence

```text
Plan/prompt ID: S0-P01B
Claim: Truth-drift checks pass on the current tree and fail on synthetic violations
Status: VERIFIED
Environment: local .venv CPython 3.12.13; pytest does not require --alembic-check
Commands:
  python -m scripts.check_truth_drift
  python -m scripts.generate_truth_artifacts --check
  python -m scripts.generate_truth_artifacts --verify-idempotent
  pytest -q apps/api/tests/test_truth_drift.py
Expected result: CLIs all [clean]; second generation byte-identical; focused pytest passes
Observed result: all 13 real-tree detectors clean; 27 truth tests passed; combined truth/object-store/Playwright guard run 32 passed, 1 warning
Artifact: contracts/*.json; contracts/truth_manifest.json; docs/verification/S0_P01B_TRUTH_DRIFT.md
Security and tenant checks: production default JWT rejected when DCLAB_ENV=production; no tenant behavior change
Rollback/kill switch: revert this prompt's scripts, contracts, CI step, and tests
Known limitations: web middleware JWT fallback allowlisted; metadata compare only after migrate
Reviewer/date: S0-P01B re-verification / 2026-09-11
```

S0-P01D subsequently verified the complete local gate and successful exact-SHA
CI; see [`S0_P01D_BASELINE_GATE.md`](S0_P01D_BASELINE_GATE.md).
