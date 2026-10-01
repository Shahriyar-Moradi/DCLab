# DCLab MVP status ledger

The only progress record. One line per prompt: status · date · SHA · evidence
(commands + result) · notes. Statuses: TODO · IN_PROGRESS · DONE · BLOCKED.

## Carry-over from Scope 0 (old program)

| Old ID | Status | Evidence | New home |
| --- | --- | --- | --- |
| S0-P01A–D truth/baseline | DONE (exact-SHA CI 91986b9, run 34598999220) | docs/verification/S0_P01*.md | — |
| S0-P02A–F sessions/BFF/CSRF | Locally verified; CI pending | S0_P02*.md | closes at Phase 0 gate CI |
| S0-P03A–E workspace/capabilities | Locally verified; P03A evidence says PENDING_FULL_GATE | S0_P03*.md | Phase 0 gate CI |
| S0-P04A–C tenancy of legacy surfaces | Locally verified; CI pending | S0_P04*.md | Phase 0 gate CI |
| S0-P04D retirement isolation gate | DONE locally on head 0061 (2026-10-01): backend matrix 41 passed / 0 skipped, full suite 1140 passed + 1 skipped, Playwright 27/27; compatibility deadline recorded (frozen now, Phase 9 decision, hard deadline 2027-03-31). Formal VERIFIED needs exact-SHA CI on the P0.2-A PR | S0_P04D_*.md | P0.2-A |
| S0-P05A policy bootstrap | Locally verified at 0060 | S0_P05A_*.md | Phase 0 gate CI |
| S0-P05B quarantine publication | DONE via P0.2-B (ADR 0005) | S0_P05B_*.md | P0.2-B |
| S0-P05C–E retention/deletion | Moved to Phase 8 (P8.7 data safety) | — | P8.7 |
| S0 Plan 0.6 `/v1` foundations | Not started | — | P3.1 |
| S0 Plan 0.7 CI/dev parity | Not started | — | P0.1, P0.3 |
| S0 Plan 0.8 ADR/risk baseline | Not started | — | ADRs 0005–0008 as phases need them |
| S0 Plan 0.9 DataScan (DuckDB) | Not started | — | Phase 9 trigger (dataset > memory cap) |
| S0 Plan 0.10 portable storage | Not started | — | P1.3 |

## Setup done on 2026-10-01 (this session)

| Item | Status | Notes |
| --- | --- | --- |
| docs/mvp program (architecture, agents, roadmap, guide, prompts) | DONE | replaces Scope 0–10 order |
| Claude Code setup: CLAUDE.md, .claude/rules, agents, skills, settings | DONE | see EXECUTION_GUIDE.md |
| "Paused" banners on docs/agentic-program + root master docs | DONE | part of P0.4-A pulled forward |

## Phase 0

| Prompt | Status | Date | SHA | Evidence |
| --- | --- | --- | --- | --- |
| P0.1-A | DONE locally — awaiting commit + CI | 2026-10-01 | (pending) | Fresh venv SQLAlchemy 2.1.1 + psycopg 3.3.6: `alembic upgrade head` on empty DB → 0061 OK; full backend+SDK suite 1136 passed / 4 skipped / 1 failed (`test_truth_drift` product-SHA rule, satisfied only once code + `S0_P01A_CURRENT_TRUTH.md` land in the same commit); truth artifacts regenerated (were already stale from the Base44 UI PR); banned-terms scan clean |
| P0.1-B | DONE locally — awaiting commit + CI | 2026-10-01 | (pending) | Metadata cycle fixed (`use_alter` + Postgres-default names on `execution_requests.pipeline_run_id`, `ingestion_runs.execution_request_id`): `compare_metadata` on migrated DB cycle warnings 1→0, schema diffs 0→0 (no migration); `test_metadata_cycle.py` passes (raises SAWarning without the fix). `requirements.lock` (universal, 223 lines) wired into CI, both Dockerfiles, `make lock`; fresh venv via plain `pip install -r requirements.lock && pip install -e . --no-deps` → SQLAlchemy 2.1.1 / psycopg 3.3.6, `pip check` clean, `alembic upgrade head` on empty DB → 0061, 57 tests passed (metadata cycle, execution requests, DB foundation gate, historical alembic revisions, SDK) + 1 failed (`test_truth_drift`: only the product-SHA rule, resolves when code and `S0_P01A_CURRENT_TRUTH.md` are committed together). Node 22 in both CI jobs; empty root `package-lock.json` removed; truth artifacts regenerated; banned-terms scan clean. NOT verified: Docker image build, GitHub CI run |
| P0.2-A | DONE locally — awaiting commit + CI | 2026-10-01 | (pending) | Re-verification on head 0061 recorded in `docs/verification/S0_P04D_RETIREMENT_ISOLATION_GATE.md`: backend matrix 41 passed/0 skipped (disposable PG16 on 55432); full backend+SDK 1140 passed/1 skipped/1 failed (stale `truth_manifest.json` after docs edits, regenerated); Playwright session-security + whole-system 27/27 (Chrome channel); web tsc 0, lint 0 errors/4 warnings, components 5/5; drift clean. No regression from 0060/0061 found, so no code repair. Deadline policy added to gate doc, archive runbook, ARCHITECTURE §6, ROADMAP Phase 9. Exact-SHA CI link: pending |
| P0.2-B | DONE locally (batched push) | 2026-10-01 | (pending) | ADR 0005 `internal_training`: own-workspace Labs uploads published after structural validation (5 audited events, labels restricted / LLM deny / policy source); production upload re-enabled; structurally invalid files rejected 422 with reason and not retained; legacy rows redacted with a quarantine message. Security review (no blockers) led to: attestation bound to an upload by the same user, production refuses LLM flags until dataset policy is enforced at call sites, mid-transaction `seed_dogfood` commit removed (atomic upload), zero-column datasets governed by their dataset default. Verified: full backend+SDK 1141 passed / 1 skipped before final fixes, then affected files 91 passed (only the per-commit SHA drift rule pending until commit); Playwright session-security + whole-system + model-build 28/28 with real uploads; publication suite 22/22 |
| P0.3-A | DONE locally (batched push) | 2026-10-01 | (pending) | `worker` service added to `docker-compose.base44.yml` and `docker-compose.yml` (`dclab worker run`); API uses `ML_JOB_DISPATCHER=postgres`; `make run` defaults to postgres (thread is explicit opt-in); `PG_BIN ?=` overridable; AGENTS.md updated. Verified: `docker compose config` lists worker in both files; native topology run (API on postgres dispatcher + separate worker, real 300-row CSV upload) → `labs.auto_train` claimed by the worker, completed on attempt 1 in ~6 s, no training in the API process. Docker image build itself not run |
| P0.4-A | DONE locally (batched push) | 2026-10-01 | (pending) | 30 case-study CSVs (~484 MB) untracked and gitignored — they are generated outputs of `benchmarks/dclab_runner.py:_materialize_frame` (synthetic generators / Olist), no test reads them, local copies kept; business proposal `.docx`/`.pdf`, `Decision_AI_Agent_Coding_Context.docx` and its generator `doc.py` moved to `docs/archive/` (`.md` proposals left in place to keep links); PAUSED banners done earlier; demo CTA uses `NEXT_PUBLIC_CONTACT_EMAIL` or falls back to `/company` (no guessed address). Web tsc 0, lint 0 errors, components 5/5 |
| P0.1-C | IN_PROGRESS — fix verified locally, awaiting CI | 2026-10-01 | (pending) | Unplanned: PR #13 CI `regression` job passed migrations, truth drift and the full backend suite (first time since 2026-09-12) but failed at `audit_client_surface` ("no opportunity available") — latent since S0-P03A: bearer callers must send `X-Workspace-Id` (API returns 400 without it, 200 with it, reproduced with curl) and the audit never did. Fix: the script discovers its workspace via `GET /v1/workspaces` and sends the selector. Local CI-like run (migrate, 2 users, model seed, API + built web): unpatched FAIL, patched PASS 17/17 operations + 10/10 pages. The E2E job was skipped behind this failure, so its first real CI run is still ahead |

## Phase 1

| Prompt | Status | Date | SHA | Evidence |
| --- | --- | --- | --- | --- |
| P1.1-A | DONE locally (batched push) | 2026-10-01 | (pending) | All admin Lab training (`/admin/datasets/{id}/use-cases/{slug}/train`, `/admin/datasets/{id}/train`, `POST /admin/experiments`, CLI `dclab experiment run --actor`) queues an open-ingest build (202) through the same `save_upload` path as Labs uploads with an explicit target; the worker trains it. Legacy re-run → 410; locked evidence re-run → 409. ML review found a blocking leakage risk — other use cases' labels would become features — fixed by excluding sibling planned targets before the build (recorded as `excluded_columns` provenance) and tested. Admin plan polls while a run is unfinished; button labels corrected. No production caller of `strategy="use_case"` remains (legacy runner branch deleted in P1.2). Verified: full backend+SDK 1146 passed / 1 skipped / 2 failed → both fixed (evidence-lock 409 restored; truth snapshot regenerated), then lab + evidence-lock 20/20; Playwright 28/28. Follow-ups (non-blocking review findings): each admin build stores its own dataset copy (graph work in Phase 2 should reuse the published dataset); open-ingest re-infers entity/time instead of the plan's hints; no idempotency across double-clicks / non-atomic train-all; identifier-like explicit target should fail fast with 400 |
| P1.2-A | DONE locally (batched push) | 2026-10-01 | (pending) | Legacy runner branch deleted (~410 lines incl. full-frame profile/leakage/encode before split, fillna(0) matrices, resampled 'robustness folds'). One engine: legacy strategies are normalized onto open-ingest using the task's declared feature groups. Re-scoped by founder-approved compatibility window: `app/ml` + factory stay frozen (Decision.ai URLs live until 2027-03-31) — `factory.py` moved `engine/experiments/` → `app/ml/`, engine owns ensemble/selection (`app/ml` re-exports), `engine/` has zero `app.ml`/services/db/api imports. ML review found a blocking issue — declared time tasks with < 8 snapshots could silently get a random split and panel tasks would fail — fixed with an explicit `temporal_future` holdout (strict chronology tested), loud failure on < 2 times, and column roles inferred on the train partition only (also fixes dropped datetime features). Verified: full backend+SDK 1150 passed / 1 skipped / 1 failed (truth snapshot, regenerated); Playwright 28/28. Follow-ups: CV folds inside train may be non-temporal on few dates; benchmarks' baselines use a different split than the engine (fix in R1); warn when declared columns are dropped; frozen `app/ml/factory.py` selects its ensemble on test (pre-existing, Phase 9) |

## Agent / Jev release decisions

| Agent or purpose | Decision | Evidence |
| --- | --- | --- |
| (filled at P6.8-A) | | |
