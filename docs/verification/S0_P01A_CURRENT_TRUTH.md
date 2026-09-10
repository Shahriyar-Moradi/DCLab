# S0-P01A current repository truth

**Status:** CURRENT  
**Plan/prompt:** S0-P01A  
**Recorded:** 2026-09-10  
**Checkout SHA:** `49da76b9f4ee6dab6579c7b933011f0641553cc5`  
**Branch:** `main` = `origin/main` (`git@github.com:Shahriyar-Moradi/DCLab.git`)  
**Last product change:** `1bce168327e1a159d4804a268043720b80630013` (PR #11, canonical target intent and `needs_input`)  
**This SHA vs product:** `49da76b` adds only `docs/agentic-program/**` on top of `1bce168`. Application behavior at that SHA is unchanged. Current Alembic head is `0058_simulation_workspace`.

This file replaces stale executive counts in older reports as **current truth**. Those older reports remain immutable historical evidence; see the [status ledger](README.md).

Do not treat this document as a license to add an agent, MCP, or HITL framework.

## How to reproduce

Read-only by default. The recorder does not migrate databases, mutate git, or write application files.

```bash
# Mechanical facts (stdout JSON)
.venv/bin/python -m scripts.record_repo_truth

# Backend + SDK
.venv/bin/pytest -q --tb=line

# Python static checks that exist in this repository
.venv/bin/python -m scripts.scan_banned_terms
.venv/bin/python -m scripts.check_object_store_untracked
.venv/bin/python -m scripts.check_playwright_untracked
.venv/bin/python -m scripts.check_client_lab_schema_contract

# Frontend — typecheck must not run in parallel with next build
cd apps/web
npx tsc --noEmit
npm run lint
npm run build
```

Browser E2E is the GitHub Actions `Whole-system E2E` job on **this SHA**. Local `npm run e2e` would duplicate that required check.

## Mechanical facts

| Evidence | Observed |
| --- | --- |
| Git | `main` tracking `origin/main` at `49da76b`. Working tree was clean at SHA; this prompt adds documentation and `scripts/record_repo_truth.py`. |
| Alembic heads | **1** head: `0054_execution_needs_input` |
| Migration revisions | **54** version files (`0001`–`0054`); **16** frozen `alembic_frozen/rev_*.py` including `rev_0054_execution_needs_input.py` |
| SQLAlchemy tables | **64** (physical PipelineRun remains `experiments`) |
| Catalog vs 0053 freeze | 0053 freeze counted **65** public heaps including `alembic_version`. 0054 adds **no tables**. Counts reconcile. |
| OpenAPI | **150** paths, **157** operations |
| `/v1` operations | **13** (list below) |
| Tracked files | **760** at SHA `49da76b` (this prompt’s new files are untracked until committed) |
| Python / TS+TSX files | **431** / **153** |
| Python / TS+TSX lines | **97,308** / **14,933** |
| Test files | **108** (`104` API, `2` SDK, `2` Playwright specs) |
| Packages | `packages/dclab_client` only |
| Toolchain (local) | CPython **3.12.13**, Node **24.11.1**, npm **11.6.2**, Next **15.5.25**. CI uses Python **3.12** and Node **20**. |

### `/v1` operations (runtime OpenAPI)

| Method | Path |
| --- | --- |
| GET | `/v1/me` |
| GET | `/v1/workspaces` |
| GET | `/v1/projects` |
| GET | `/v1/projects/{project_id}` |
| GET | `/v1/datasets` |
| GET | `/v1/datasets/{dataset_id}` |
| POST | `/v1/execution-requests` |
| GET | `/v1/execution-requests/{request_id}` |
| POST | `/v1/execution-requests/{request_id}/target-confirmation` |
| GET | `/v1/model-builds/{pipeline_run_id}` |
| GET | `/v1/model-builds/{pipeline_run_id}/artifacts` |
| GET | `/v1/model-builds/{pipeline_run_id}/events` |
| GET | `/v1/model-builds/{pipeline_run_id}/visualizations` |

### OpenAPI prefix counts

| Prefix | Operations |
| --- | --- |
| `/admin` | 76 |
| `/workspaces` | 31 |
| `/app` | 17 |
| `/business` | 15 |
| `/v1` | 13 |
| `/auth` | 3 |
| `/development` | 1 |
| `/health` | 1 |

## Gate results (this pass)

| Gate | Status | Observed |
| --- | --- | --- |
| Backend + SDK pytest | **VERIFIED** | `945 passed, 3 skipped, 21 warnings` in **515.51s**. Isolated `decisionai_test` on localhost:5432. |
| Python ruff/mypy/flake8 | **NOT_TESTED** | Those tools are not configured in this repository. |
| Banned-terms scan | **VERIFIED** | `python -m scripts.scan_banned_terms` clean |
| Object-store untracked | **VERIFIED** | clean |
| Playwright dirs untracked | **VERIFIED** | clean |
| Labs Zod/Pydantic contract | **VERIFIED** | `ClientLabUploadSchema` ↔ `ClientLabUploadRead`; `LabRunOutcomeSchema` ↔ `ClientLabRunOutcome` |
| `npx tsc --noEmit` | **VERIFIED** | exit 0 (run **before** `next build`) |
| `npm run lint` | **VERIFIED** | exit 0; **3** `react-hooks/exhaustive-deps` warnings in Model Build inspector/stage panels |
| `npm run build` | **VERIFIED** | 31 static pages generated; **48** listed routes including `/_not-found` |
| Local Playwright E2E | **NOT_TESTED** | Duplicate of required CI; not re-run locally |
| GitHub CI run 52 | **VERIFIED** | [actions/runs/34496414362](https://github.com/Shahriyar-Moradi/DCLab/actions/runs/34496414362) on SHA `49da76b`. `regression` success; `Whole-system E2E` success (Playwright step 15:49:27–15:51:54Z) |
| Live OpenAI / `DECISION_AGENT_LIVE` | **NOT_TESTED** | skipped by design this pass |
| Isolated Postgres **55432** tests | **NOT_TESTED** | two tests skip when that cluster is absent (same skip in GitHub CI) |
| `git diff --check` | **VERIFIED** | no whitespace errors on tracked diffs |

### Skipped pytest (3)

| Test | Why | Classification |
| --- | --- | --- |
| `test_live_smoke_request_decision` | needs `DECISION_AGENT_LIVE=1` and a real API key | **NOT_TESTED** |
| `test_legacy_upload_lineage_is_repaired_without_moving_unlinked_rows` | hardcoded isolated Postgres on **55432** | **NOT_TESTED** |
| `test_postgres_append_only_trigger_and_sequence_uniqueness` | hardcoded isolated Postgres on **55432** | **NOT_TESTED** |

Historical Alembic upgrade tests that create databases on **5432** passed in this suite (`test_historical_alembic_revisions.py` and related).

## 0053 freeze vs current 0054

The 2026-09-09 database architecture freeze is **HISTORICAL** at `0053_pipeline_run_branch`. It is not silently current.

| Topic | 0053-era audit | Current head `0054_execution_needs_input` |
| --- | --- | --- |
| What 0054 changes | (not present) | CHECK vocabularies only: `execution_requests.status` and `client_lab_uploads.client_status` gain `needs_input`. No new tables, FKs, or triggers. |
| Tables | 65 public heaps including `alembic_version` | 64 SQLAlchemy mapped tables + `alembic_version` = same catalog width |
| FK / index / trigger counts | 332 FKs, 80 uniques, 350 indexes, 29 user triggers, no RLS | **IMPLEMENTED** as the 0053 freeze; **NOT_TESTED** as a fresh pg_catalog dump this prompt (0054 cannot change those objects) |
| Unresolved Case D (ambiguous target) | pre-0054 product treated this as failure in older Labs paths | **VERIFIED**: status `needs_input` on ExecutionRequest and ClientLabUpload; WorkflowRun stays running; waiting MlJob attempt completes; resume requeues the same ExecutionRequest via `POST /v1/execution-requests/{id}/target-confirmation` body `{ "target_column": "..." }` and Labs `POST /app/labs/uploads/{upload_id}/target-confirmation` |
| Canonical target intent | later PR #11 on `1bce168` | **VERIFIED** in `test_target_intent.py` and `test_execution_target_confirmation.py`. Ambiguity is not first-tied-column. LLM does not authorize the column. |
| Physical PipelineRun | `experiments` / `experiments.id` as `pipeline_run_id` | **VERIFIED** unchanged |
| `compare_metadata()` | DIFF_COUNT 0 at 0053 | **VERIFIED** via historical Alembic tests in this pytest run (warnings: FK cycle among `datasets`, `execution_requests`, `experiments`, `ingestion_runs`) |

## Claim ledger

Statuses: **VERIFIED** (command + observed evidence this pass), **IMPLEMENTED** (in tree, not re-proved here), **PARTIAL**, **PLANNED**, **BLOCKED**, **NOT_TESTED**.

| ID | Claim | Status |
| --- | --- | --- |
| T-001 | Current `main` SHA is `49da76b` and equals `origin/main` | **VERIFIED** |
| T-002 | Last application SHA is `1bce168`; `49da76b` is docs-only | **VERIFIED** |
| T-003 | Single Alembic head `0054_execution_needs_input` | **VERIFIED** |
| T-004 | 54 Alembic version files, 16 frozen revision modules | **VERIFIED** |
| T-005 | 64 SQLAlchemy tables; PipelineRun is `experiments` | **VERIFIED** |
| T-006 | 157 OpenAPI operations, 150 paths, 13 `/v1` operations | **VERIFIED** |
| T-007 | 760 tracked files; 431 Python; 153 TS/TSX; ~97,308 / 14,933 lines | **VERIFIED** |
| T-008 | Backend+SDK `945 passed, 3 skipped` | **VERIFIED** |
| T-009 | CI run 52 regression + whole-system E2E succeeded on `49da76b` | **VERIFIED** |
| T-010 | Frontend lint/tsc/production build succeed (3 hook warnings) | **VERIFIED** |
| T-011 | `needs_input` is a resumable control-plane wait, not scientific failure | **VERIFIED** |
| T-012 | 0053 architecture freeze remains accurate for catalog width after 0054 | **VERIFIED** (constraint-only revision) |
| T-013 | `DCLAB_SYSTEM_VERIFICATION_REPORT.md` executive counts (Alembic 0027, 498 pytest, 6 Playwright, 29 routes) are current | **PLANNED** replacement: those numbers are **HISTORICAL** (2026-09-04). Do not use them. |
| T-014 | `DCLAB_API_REFERENCE.md` “94 operations” is current | superseded; **HISTORICAL**. Current is 157. |
| T-015 | `docs/verification/BASELINE.md` SHA `de2af56` is current | **HISTORICAL** pre-repair baseline |
| T-016 | `DCLAB_DATABASE_ERD.md` at 0039 is the current ERD | **HISTORICAL** diagram. Current head is 0054. |
| T-017 | Master plan §3.1 facts at `1bce168` (945 pytest, 64 tables, 157/150/13 HTTP, 746 files) | **PARTIAL**: HTTP/schema/pytest still match; tracked files are now **760** because of agentic-program docs |
| T-018 | No user-visible agent runtime exists | **VERIFIED** (no AgentDefinition/Session tables; `/v1` has no agent routes) |
| T-019 | Browser stores a JavaScript-readable `dclab_token` bearer cookie | **VERIFIED closed** by S0-P02A (HttpOnly `dclab_session` + BFF; see [S0_P02A_BROWSER_SESSIONS.md](S0_P02A_BROWSER_SESSIONS.md)) |
| T-020 | Web client does not send `X-Workspace-Id` | **IMPLEMENTED** (gap for S0-P03) |
| T-021 | `SimulationRun` has no workspace | **IMPLEMENTED** (gap for S0-P04) |
| T-022 | Alembic metadata cycle warning among four tables | **VERIFIED** as warning; repair is **PLANNED** (S0-P08), not this prompt |
| T-023 | Playwright suite is 6 tests / 29 Next routes | **HISTORICAL**. Current: **13** Playwright tests in 2 specs; **48** listed Next routes / 31 static pages |
| T-024 | Live OpenAI Luna/Terra from the 2026-09-04 report | **NOT_TESTED** this pass |
| T-025 | LibreOffice DOCX visual | **NOT_TESTED** |
| T-026 | Frontend unit-test script | **NOT_TESTED** / not applicable — `apps/web` has no unit-test script |
| T-027 | Deterministic ML selection, holdout lock, evidence lock, tenant FKs | **VERIFIED** by the 945-passing suite (not re-described here) |
| T-028 | Truth-drift CI (multi-head, OpenAPI snapshot, stale-status docs) | **VERIFIED** — S0-P01B `python -m scripts.check_truth_drift`; see [S0_P01B_TRUTH_DRIFT.md](S0_P01B_TRUTH_DRIFT.md) |
| T-029 | README “SSO/Kubernetes out of scope” without program context | **IMPLEMENTED** this prompt: README now points at Scopes 9–10 |

## Evidence records

```text
Plan/prompt ID: S0-P01A
Claim: Mechanical repository facts at HEAD
Status: VERIFIED
Commit/image digest: 49da76b9f4ee6dab6579c7b933011f0641553cc5
Environment: local macOS, .venv CPython 3.12.13, Postgres 16 on localhost:5432
Migration path tested: pytest historical Alembic tests to head 0054 (not a freeze-DB recreate this prompt)
Commands: python -m scripts.record_repo_truth
Expected result: one Alembic head 0054; 64 tables; 157 ops / 150 paths / 13 /v1
Observed result: matches
Artifact/log/dashboard link: docs/verification/S0_P01A_CURRENT_TRUTH.md
Security and tenant checks: not in scope for the recorder; covered by pytest access/lineage tests
Rollback/kill switch: delete this prompt's documentation and scripts/record_repo_truth.py; no schema or app rollback
Known limitations: recorder is read-only; does not dump pg_catalog
Reviewer/date: S0-P01A / 2026-09-10
```

```text
Plan/prompt ID: S0-P01A
Claim: Backend and SDK suite still green after 0054 / PR #11
Status: VERIFIED
Commit/image digest: 49da76b9f4ee6dab6579c7b933011f0641553cc5
Environment: DATABASE_URL default → decisionai_test on localhost:5432
Commands: .venv/bin/pytest -q --tb=line
Expected result: 945 passed, 3 skipped
Observed result: 945 passed, 3 skipped, 21 warnings, 515.51s
Security and tenant checks: included in suite (access control, lineage, evidence lock)
Known limitations: 55432-only tests skipped; live LLM skipped
Reviewer/date: S0-P01A / 2026-09-10
```

```text
Plan/prompt ID: S0-P01A
Claim: Same-SHA required CI including browser E2E
Status: VERIFIED
Commit/image digest: 49da76b9f4ee6dab6579c7b933011f0641553cc5
Environment: GitHub Actions ubuntu-latest, Postgres 16 service, Python 3.12, Node 20
Commands: workflow .github/workflows/ci.yml (regression then Whole-system E2E)
Expected result: both jobs success on this SHA
Observed result: run 34496414362 success; E2E Playwright step completed 2026-09-10T15:51:54Z
Artifact/log/dashboard link: https://github.com/Shahriyar-Moradi/DCLab/actions/runs/34496414362
Known limitations: local Playwright not duplicated
Reviewer/date: S0-P01A / 2026-09-10
```

## Limitations

- This prompt did not change application behavior.
- The 760 tracked-file count is the committed tree at `49da76b`. New files from this prompt (`scripts/record_repo_truth.py`, `docs/verification/README.md`, `docs/verification/S0_P01A_CURRENT_TRUTH.md`) are untracked until a later authorized commit.
- pg_catalog object counts (FKs, triggers) were not re-dumped; they are inherited from the 0053 freeze plus the 0054 CHECK-only revision.
- Isolated 55432 verification cluster was not started.
- Live OpenAI was not invoked.
- Historical report bodies were not rewritten.

## Next prompt

S0-P01B is implemented (`docs/verification/S0_P01B_TRUTH_DRIFT.md`). Next eligible prompt is **S0-P02A**.
