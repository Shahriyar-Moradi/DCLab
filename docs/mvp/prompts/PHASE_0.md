# Phase 0 — Stabilize the foundation

### P0.1-A — Green, reproducible backend dependencies
Model: Sonnet 5.5 (medium) · Size: S · Depends on: — · Review: test-runner
Goal: CI installs the same dependency set as local dev and passes migrations.
Read: `pyproject.toml`, `.github/workflows/ci.yml`, `Dockerfile`, `Dockerfile.base44.api`, `apps/api/app/config.py`, `apps/api/app/services/execution_request_service.py:100-125`, `packages/dclab_client/tests/test_client_isolation.py`.
Do:
- Replace `psycopg2-binary` with `psycopg[binary]>=3.2,<4`; bound `sqlalchemy>=2.0.30,<2.2`; add sane upper bounds for pandas/scikit-learn/numpy/pyarrow.
- Normalize the DB URL in `config.py` (`postgresql://` → `postgresql+psycopg://`) with a validator so every env/compose/CI URL keeps working.
- Make the constraint-name extraction in `execution_request_service.py` read `diag.constraint_name` and `sqlstate` (fallback `pgcode`).
- Move `pytest`, `pytest-cov`, `python-docx` (if only used by scripts/tests) to a `dev` extra; CI installs `.[boosting,dev]`.
- Remove the extra `pip install psycopg` workaround from `Dockerfile.base44.api`; add `psycopg` to the SDK forbidden-imports list.
- Delete stale `apps/api/decision_ai.egg-info` from the working tree if untracked.
Don't: change application behavior, upgrade unrelated libraries, touch migrations.
Verify: fresh venv `pip install -e ".[boosting,dev]"`; `alembic upgrade head` on an empty DB; `pytest apps/api/tests/test_execution_requests.py apps/api/tests/test_database_foundation_gate.py packages/dclab_client/tests -q`.
Done when: tests pass on SQLAlchemy 2.1 + psycopg3 locally and the CI regression job passes.

### P0.1-B — Lockfile, metadata cycle, CI parity
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P0.1-A · Review: db-migration-reviewer
Goal: deterministic installs and a warning-free metadata graph.
Do:
- Generate `uv.lock` (or `requirements.lock` via pip-tools) and use it in CI and Dockerfiles.
- Add `use_alter=True` (or remove the duplicate) on single-column FKs `ingestion_runs.execution_request_id` (`db/models.py:~2152`) and `execution_requests.pipeline_run_id` (`~1629`) so the "unresolvable cycles" SAWarning disappears; no migration if DDL is unchanged — prove with `alembic check`.
- Unify Node to 22 LTS in both CI jobs; fix root empty `package-lock.json` multiple-lockfile warning.
- Founder decision needed: `scripts/truth_drift.py:check_current_status_docs` forces every product commit to also edit `docs/verification/S0_P01A_CURRENT_TRUTH.md`. Recommended: drop the per-commit product-SHA rule and record exact SHAs only at phase gates in `docs/mvp/STATUS.md` (update the check's tests accordingly).
Verify: `python -W error::sqlalchemy.exc.SAWarning -c "import app.db.models"`; `alembic check`; full CI run.
Done when: CI green on `main`.

### P0.1-C — Live client audit sends the workspace selector
Model: Sonnet 5.5 (medium) · Size: XS · Depends on: P0.1-B · Review: test-runner
Goal: the CI step `audit_client_surface` passes now that bearer callers must send `X-Workspace-Id` (ADR 0003).
Do: `scripts/audit_client_surface.py` discovers the client's workspace via `GET /v1/workspaces` and sends the selector on every bearer request; fail with a clear message if none; report the HTTP status when no opportunity is found.
Done when: unpatched run fails and patched run passes locally with the CI seed steps, then CI `regression` is green. (Added after PR #13's first CI run.)

### P0.2-A — Close S0-P04D (legacy retirement isolation gate)
Model: Opus 5.5 (high) · Size: M · Depends on: P0.1-B · Review: security-reviewer
Goal: formally close S0-P04D on the current head (0061).
Read: `docs/verification/S0_P04D_RETIREMENT_ISOLATION_GATE.md`, `docs/agentic-program/prompts/SCOPE_00_FOUNDATION.md:288-325`, ADR 0004.
Do: record the deprecation/compatibility deadline for the legacy archive surfaces (founder decision: freeze now, removal at Phase 9 business-layer rework, no later than 2027-03-31); re-run the P04D matrix on head 0061; fix any regressions caused by 0060/0061.
Verify: the P04D test matrix named in the evidence file + Playwright subset; exact-SHA CI.
Done when: STATUS.md marks S0-P04D DONE with SHA and CI run link.

### P0.2-B — Close S0-P05B with an explicit upload policy
Model: Opus 5.5 (high) · Size: M · Depends on: P0.2-A · Review: security-reviewer, db-migration-reviewer
Goal: resolve the blocked product decision so uploads have one clear lifecycle.
Decision to implement (write ADR 0005-upload-policy): owner/member uploads to their own workspace are published as `internal_training` after structural validation (type, size, encoding, schema) — no content scanner in the MVP; quarantine remains the state for failed validation; external sharing/export of data stays denied. Scanner/classifier/resume worker move to Phase 8.
Do: finish the fail-closed publication path from S0-P05B with this policy; UI shows quarantine reason.
Verify: `test_ingestion_publication*`, upload E2E, access-control sweep.
Done when: STATUS.md marks S0-P05 DONE; remaining 0.5C–E items listed as Phase 8 work.

### P0.3-A — Real worker in dev topology
Model: Sonnet 5.5 (medium) · Size: S · Depends on: P0.1-A · Review: test-runner
Goal: dev matches production execution semantics.
Do: add a `worker` service (`dclab worker run`) to `docker-compose.base44.yml` and `docker-compose.yml`; set `ML_JOB_DISPATCHER=postgres` there; keep `thread` only as an explicit opt-in documented in AGENTS.md; make Makefile Postgres path overridable (`PG_BIN ?=`) for Linux.
Verify: `docker compose -f docker-compose.base44.yml up -d --build`, upload sample CSV via UI/API, run completes via worker logs.
Done when: training happens only in the worker container.

### P0.4-A — Repository hygiene
Model: Sonnet 5.5 (low) · Size: S · Depends on: — · Review: none
Goal: lighter checkout, honest docs index.
Do:
- Move `data/case_studies/**/*.csv` out of git: add `scripts/fetch_case_studies.py` (source URL or regenerate script + SHA-256 manifest), `.gitignore` them, update tests/benchmarks to call the fetch or skip with reason.
- Add a "PAUSED — see docs/mvp" banner to `docs/agentic-program/README.md`, `MASTER_SCOPE_0_TO_10_PLAN.md`, root `DCLAB_MASTER_IMPLEMENTATION_SPEC.md`, `DCLAB_MASTER_CONTEXT.md`.
- Move business proposal `.docx/.pdf` and `Decision_AI_Agent_Coding_Context.docx`/`doc.py` to `docs/archive/` (or out of repo if founder prefers).
- Replace `hello@decision.ai` demo link with DCLab contact.
Don't: rewrite git history (that is a P8.6 decision).
Verify: `pytest -q -x` subset touching case studies; `git ls-files | xargs du -ch | tail -1` shows the reduction.
