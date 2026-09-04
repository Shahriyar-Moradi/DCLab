# DCLab System Verification Report

**Baseline SHA:** `de2af56824b81400624f34758308324db34f4da9` (`main`, recorded in [verification/BASELINE.md](verification/BASELINE.md))  
**Verification date:** 2026-09-04  
**Isolated PostgreSQL:** `localhost:55432` (`artifacts/e2e-verification-pgdata`)  
**Authoritative Python:** repository `.venv` CPython 3.12.13 (CI is 3.12; host also has 3.14)  
**Authoritative Node for this pass:** 24.11.1 locally (CI is 20)  
**Overall status:** **PASS with residual P2/P3**

P0 and P1 defects that were reproduced in this pass are repaired and retested.
Remaining items are compatibility, IA duplication, incomplete capability
separation on raw event routes, or gates that this environment cannot execute.
`NOT_TESTED` is never treated as `PASS`.

Evidence artifacts (gitignored): `artifacts/e2e-verification/`.

---

## Executive counts

| Gate | Result |
| --- | --- |
| Alembic head | `0027_repair_tenant_lineage` (single head) |
| Empty + E2E DBs `alembic current` / `check` | Head; no new upgrade operations |
| `pytest apps/api/tests` on 55432 | **498 passed, 1 skipped**, 10 warnings, 232.69s |
| Skipped test | `test_live_smoke_request_decision` (`DECISION_AGENT_LIVE` not set) |
| Frontend lint | Clean |
| Frontend `tsc --noEmit` | Clean |
| Frontend `next build` | Clean (29 routes) |
| Frontend unit tests | **NOT_APPLICABLE** — `apps/web` has no unit-test script |
| Playwright `apps/web` e2e | **6 passed** (1.2m after webServer boot) |
| Banned-terms scan | Clean |
| Client Labs Zod/Pydantic contract | Clean |
| `git diff --check` | Clean |
| LibreOffice DOCX visual | **NOT_TESTED** — `soffice`/`libreoffice` not installed |
| CI live admin/client OpenAPI crawls | **NOT_TESTED** this pass (API/web not left running; no `client_user` on E2E DB) |
| 500-row dedicated performance smoke | **NOT_TESTED** (100-row classification/regression ran in Playwright and API tests) |

---

## Live OpenAI (safe fields only)

Credentials were present. Values recorded in `artifacts/e2e-verification/live-openai.json` (gitignored). The key is not logged here.

| Check | Provider | Model | Status | Latency | Digest prefix |
| --- | --- | --- | --- | --- | --- |
| `dclab verify-openai-smoke` | openai | `gpt-5.6-luna` | `VERIFIED_WITH_WARNINGS` | ~18068 ms | `5804c828…` |
| Synthetic Terra deep (`OpenAIPipelineAuditProvider.audit`) | openai | `gpt-5.6-terra` | `NOT_VERIFIABLE` (advisory) | ~19432 ms | `5804c828…` |
| Persisted E2E run Luna routine | openai | `gpt-5.6-luna` | `completed` / advisory `VERIFIED` | ~11869 ms | `1d272e3c…` |
| Persisted E2E run Terra deep | openai | `gpt-5.6-terra` | `completed` / advisory `VERIFIED` | ~9783 ms | `1d272e3c…` |

Persisted audits used a completed `client_lab_uploads` row in `dclab_e2e_verify`
after Playwright training. Deterministic status on that run was `VERIFIED`.
Advisory status did not override storage of the deterministic result.

---

## P0–P3 disposition

### P0 — fixed

| ID | Finding | Repair | Evidence |
| --- | --- | --- | --- |
| P0-1 | Observatory handlers raised `RuntimeError: workspace authorization dependency was not evaluated` | `/business` parent now depends on `require_workspace_read` as well as `require_business_administration` | `apps/api/app/main.py`; `test_pipeline_observability.py` |
| P0-2 | Legacy `client_user` could reach `/business/observatory` (404 on missing IDs) | `require_business_administration` on the `/business` parent | `test_access_control.py` `/business` sweep; observatory tests use `admin_client` for 200s and assert 403 for `client_user` |

### P1 — fixed

| ID | Finding | Repair | Evidence |
| --- | --- | --- | --- |
| P1-1 | Legacy Labs rows left on default workspace after 0023 | Forward migration `0027_repair_tenant_lineage` only; 0022–0026 untouched | `test_legacy_tenant_lineage_migration.py`; empty DB upgrade to head |
| P1-2 | `model_management` not fail-closed | 403 on Business model detail; models stripped from workspace detail; UI copy | `test_model_management_fails_closed_on_business_model_routes`; Playwright capability test |
| P1-3 | OpenAPI `/business` anonymous/`client_user` sweep omitted | Added to `test_access_control.py` | 401 anonymous, 403 `client_user` |
| P1-4 | Re-enabling a disabled `WorkspaceDomain` was a no-op | `enable_workspace_domain` sets `enabled=True`; `disable_workspace_domain` added | `test_operations_domain_can_be_enabled_disabled_and_reenabled_without_migration` |
| P1-5 | Email/phone could persist in observability payloads | Extra redaction in `sanitize_observability_payload` | `test_pipeline_observability.py` persisted-row assertions |
| P1-6 | Semantic/audit LLM purpose vs mode not structurally checked | `create_llm_invocation` rejects mismatched purpose/mode | observability tests |
| P1-7 | Reports panel not gated on `decision_ledger` | Monitor UI hides reports without the flag | Playwright + monitor page |
| P1-8 | Verifier compared encoded Yes/No `y_true` to raw artifact labels | Coerce via `coerce_binary_target` / numeric before compare | `test_encoded_binary_labels_still_match_the_input_artifact`; unblocked `test_stage_timings_feature_truth_and_persisted_report` |
| P1-9 | Playwright Next.js JWT secret ≠ API secret | Shared `e2e-verification-only-secret` in `playwright.config.ts` | 6/6 Playwright passed |

### P2 — open (documented, not inflated)

| ID | Finding | Status |
| --- | --- | --- |
| P2-1 | Next middleware and session UI use JWT `users.role`; membership tables are backend authority | Compatibility / split-brain risk if role and membership diverge |
| P2-2 | `apps/web/lib/infrastructure/api-client.ts` does not send `X-Workspace-Id` | Single-membership inference via `users.workspace_id` |
| P2-3 | `require_business_administration` keys off `users.role`, not membership | Platform/business role string must stay in sync with membership |
| P2-4 | Legacy `client_user` still receives all capabilities on `/app` | Documented compatibility |
| P2-5 | Raw `/business/observatory` events require only `pipeline_monitor` + `raw_pipeline_debug` | CV/semantic/OpenAI event types are not re-filtered |
| P2-6 | `GET /app/insights` validates workspace membership then queries simulations globally | Tenant leak risk for simulation insights |
| P2-7 | Schema allows multiple pipelines per `WorkflowRun`; production Labs creates one | Not a defect of uniqueness; automatic multi-pipeline orchestration is unsupported |
| P2-8 | Client checklist remains `/lab/runs/{id}`; staff upload detail remains `/admin/models/client-uploads/{id}` | Parallel IA, not the Business/Admin Pipeline Monitor |
| P2-9 | `/admin/organizations` still exists beside Workspace-as-Business | Duplicate navigation |
| P2-10 | Default pytest uses `Base.metadata.create_all`, not Alembic | Append-only trigger is covered only by `test_ml_run_events_postgres_enforcement.py` |
| P2-11 | Dataset asset slug uses a 12-hex prefix of the dataset UUID (0023) | Collision theoretically possible; not rewritten |

### P3 — open

| ID | Finding | Status |
| --- | --- | --- |
| P3-1 | CI still seeds two demo accounts (`admin@dclab.io`, `demo@client.io`) | Compatibility |
| P3-2 | E2E JWT secret is 28 bytes (HMAC warning) | Test-only |
| P3-3 | No frontend unit-test runner | Coverage is Playwright + TypeScript |
| P3-4 | Dedicated 500-row timing smoke not executed | 100-row runs only |
| P3-5 | LibreOffice visual DOCX | Tooling absent |

---

## Numbered claim ledger

Statuses: `PASS`, `PARTIAL`, `FAIL`, `NOT_TESTED`, `NOT_APPLICABLE`.

Chain for `PASS`: requirement → PostgreSQL schema → implementation → API → UI when applicable → automated test → runtime evidence.

### Identity, tenancy, authorization (access architecture)

| ID | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-001 | Workspace is the only tenant boundary | PASS | `workspaces` PK; FK fan-out in pg_catalog; `authorization_service` |
| C-002 | Four modern roles exist and are distinct | PASS | Check constraint on `users.role`; four seeded E2E users; HTTP + Playwright |
| C-003 | Platform developers are read-only | PASS | Method guards on `/admin` and `/app`; Playwright mutation denial |
| C-004 | Business developers are read-only | PASS | Playwright + `can_write_workspace` |
| C-005 | Cross-tenant object IDs return 404 after workspace auth | PASS | API tests + Playwright Business A vs B |
| C-006 | Unauthorized workspace selector is 403 | PASS | `test_access_architecture.py` |
| C-007 | Membership tables are authoritative on the API | PASS | `authorization_service` loads memberships |
| C-008 | UI gates match membership authority | PARTIAL | Middleware uses JWT `role` (P2-1) |
| C-009 | Frontend sends `X-Workspace-Id` | FAIL | `api-client.ts` has Authorization only (P2-2). Backend still infers a single membership workspace |
| C-010 | Legacy `client_user` remains on `/app` only | PASS | `/business` 403 after P0-2; `/app` still writable for `client_user` (P2-4) |
| C-011 | `/admin/organizations` is not a second tenant | PARTIAL | Table/API still present as platform summaries (P2-9) |

### Capabilities and domains

| ID | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-012 | Nine capability keys fail closed when missing/false | PARTIAL | Eight flags enforced on listed routes; raw events skip three filters (P2-5); no model mutation API |
| C-013 | `pipeline_monitor` gates Business monitor/observatory | PASS | Service + Playwright |
| C-014 | `cv_fold_details` / `semantic_llm_audit` / `openai_pipeline_audit` shape combined monitor | PASS | Combined monitor transformers + tests |
| C-015 | Same three flags also shape raw event APIs | FAIL | Raw handlers return `_events` after `raw_pipeline_debug` only |
| C-016 | `raw_pipeline_debug` required for raw payloads | PASS | 403 without flag |
| C-017 | `decision_ledger` gates report sections | PASS | API redaction + Playwright section text |
| C-018 | `prediction_download` gates CSV | PASS | Business and `/app` download tests |
| C-019 | `model_management` fail-closed | PASS | After P1-2 |
| C-020 | `deep_audit` + `openai_pipeline_audit` + write for Business deep verify | PASS | `test_business_administration.py` |
| C-021 | Platform roles bypass capability flags | PASS | Access tests |
| C-022 | `operations` domain can be added without migration or new routes | PASS | Catalog insert + enable/disable/re-enable test; Playwright asserts Operations on business page |

### Database and lineage

| ID | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-023 | Empty database upgrades to head | PASS | `dclab_verify_empty` / `dclab_e2e_verify` at 0027 |
| C-024 | Legacy 0021 database upgrades through 0022–0027 | PASS | `test_legacy_tenant_lineage_migration.py` |
| C-025 | Historical 0022–0026 were not rewritten | PASS | Git: only new `0027_repair_legacy_tenant_lineage.py` |
| C-026 | Physical schema matches SQLAlchemy metadata | PASS | `alembic check`; architecture doc `DIFF_COUNT 0` |
| C-027 | `ml_run_events` append-only in PostgreSQL | PASS | Trigger + UPDATE/DELETE rejection + concurrent sequences in `test_ml_run_events_postgres_enforcement.py` |
| C-028 | Dataset / ModelVersion immutability | PASS | Lineage tests (service/ORM); no DB trigger equivalent to events |
| C-029 | Cross-workspace FK violations rejected | PASS | Lineage tests |
| C-030 | Labs lineage Workspace→…→ModelVersion persisted | PASS | Upload tests + Playwright IDs + DB rows |
| C-031 | Same-content upload creates a new run (not silent overwrite) | PASS | Lineage tests |
| C-032 | Automatic production orchestration of N pipelines per WorkflowRun | NOT_APPLICABLE | Schema allows N; Labs writes one `pipeline_index` |
| C-033 | 0027 repairs unambiguous default-workspace lineage | PASS | Migration SQL + tests |

### Scientific ML pipeline

| ID | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-034 | Production entry is `POST /app/labs/uploads` | PASS | `client_labs.py` + Playwright uploads |
| C-035 | Holdout locked before CV | PASS | `test_ml2_pipeline_integrity.py` + events |
| C-036 | Train-only decisions / fold-local preprocessing | PASS | ML integrity + auto_prepare tests |
| C-037 | CV-only winner selection; winner lock before holdout | PASS | Runner + integrity tests |
| C-038 | Exactly one winner holdout evaluation | PASS | Integrity tests |
| C-039 | Classification and regression both complete | PASS | Playwright 100-row CSVs + API ML suites |
| C-040 | Forced candidate failure remains visible | PASS | Pipeline/observability tests |
| C-041 | Failed run does not publish a winner | PASS | Lineage / auto-train tests |
| C-042 | Deterministic verifier is authoritative | PASS | Corruption tests in `test_pipeline_verifier.py`; conservative overlay in audit service |
| C-043 | Advisory OpenAI cannot upgrade a deterministic failure | PASS | Audit service + observability tests |
| C-044 | 100-row smoke | PASS | Playwright classification/regression |
| C-045 | 500-row performance smoke | NOT_TESTED | Not executed as a dedicated timing gate |

### Observability, replay, LLM

| ID | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-046 | Events persist in stage order and replay stably | PASS | Observatory incremental + Playwright monitor refresh |
| C-047 | Secrets/PII/raw rows not stored in event payloads | PASS | Sanitizer + DB-row tests after P1-5 |
| C-048 | Semantic no-LLM vs forced-ambiguity LLM records | PASS | Observability tests |
| C-049 | Purpose/mode constraints | PASS | After P1-6 |
| C-050 | PostgreSQL sequence uniqueness under concurrency | PASS | Postgres enforcement test |
| C-051 | Live Luna routine against synthetic evidence | PASS | Smoke CLI |
| C-052 | Live Terra deep against synthetic evidence | PASS | Provider call completed (`NOT_VERIFIABLE` advisory is a model judgment, not a transport failure) |
| C-053 | Live Luna/Terra against a persisted Labs run | PASS | `request_pipeline_verification` on E2E completed upload |

### Frontend / browser

| ID | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-054 | DCLab Admin sees Business A and B, trains classification, monitor replay | PASS | Playwright test 1; `classification-monitor.png` |
| C-055 | DCLab Developer reads both businesses, cannot mutate | PASS | Playwright test 2; `readonly-developer.png` |
| C-056 | Business Admin trains regression, tenant-scoped | PASS | Playwright test 3; `regression-monitor.png` |
| C-057 | Capabilities fail closed in API and UI | PASS | Playwright test 4 |
| C-058 | Business Developer tenant-scoped read-only | PASS | Playwright test 5 |
| C-059 | Business A cannot substitute Business B IDs | PASS | Playwright test 6 |
| C-060 | Failed-run monitor captured | PASS | `failed-run-monitor.png` |
| C-061 | Monitor shows stages, CV comparison, winner, verifier, predictions, timeline, reports, sanitized evidence | PASS | Playwright assertions (`exact` h2 headings) |
| C-062 | No HTTP mocking in E2E | PASS | Real FastAPI + Next `webServer` in `playwright.config.ts` |

### Documentation fidelity

| ID | Claim | Status | Evidence |
| --- | --- | --- | --- |
| C-063 | API reference matches runtime OpenAPI (94 ops) | PASS | Regenerated from `app.openapi()` then corrected for `/business` guards |
| C-064 | RBAC matrix matches backend checks | PASS | Includes fail-closed `model_management` and raw-event partial |
| C-065 | Pipeline deep dive has 90 implementation-linked topics | PASS | [DCLAB_PIPELINE_DEEP_DIVE.md](DCLAB_PIPELINE_DEEP_DIVE.md) |
| C-066 | Architecture docs that were disproved were corrected | PASS | Access + observability docs updated for `/business` deps; 0027 noted in database architecture |

---

## Material-claim checklists (five architecture/admin documents)

Corrected only where runtime evidence disproved a claim. Unverified prose is left; this section records the verdict.

### `DCLAB_ACCESS_ARCHITECTURE.md`

| Claim | Verdict |
| --- | --- |
| Workspace is the tenant; Organization is not a tenant | PASS (Organizations UI/API still exist as a parallel registry — P2-9) |
| Four-role matrix + read-only developers | PASS on backend method guards |
| `/admin` method-aware platform guard | PASS |
| `/app` workspace guard | PASS |
| `/business` observatory uses `require_business_administration` + `require_workspace_read` | PASS after repair (doc updated) |
| `X-Workspace-Id` is a selector, never proof | PASS on backend; frontend does not send it |
| 403 vs 404 conventions | PASS |

### `DCLAB_PLATFORM_ADMINISTRATION.md`

| Claim | Verdict |
| --- | --- |
| Hierarchy Business → Domain → Workflow → Run → Pipeline → Model → Monitor | PASS for implemented explorer routes |
| Domain navigation from catalog, not five hard-coded domains | PASS |
| Workflow run can show more than one pipeline | PARTIAL — UI/schema can; Labs does not auto-create multiple |
| Monitor is persisted events, not a fake delay | PASS |
| `dclab_developer` writes blocked | PASS |

### `DCLAB_BUSINESS_ADMINISTRATION.md`

| Claim | Verdict |
| --- | --- |
| Same lineage records as platform | PASS |
| Cross-tenant 404 | PASS |
| Capability fail-closed | PARTIAL — see C-012/C-015 |
| `model_management` | PASS after P1-2 |
| Platform roles bypass flags | PASS |

### `DCLAB_DATA_AND_MODEL_LINEAGE.md`

| Claim | Verdict |
| --- | --- |
| Canonical hierarchy tables exist | PASS (`DCLAB_DATABASE_ARCHITECTURE.md` + models) |
| Workflow ≠ WorkflowRun ≠ Experiment ≠ Candidate ≠ ModelVersion | PASS |
| Legacy default-workspace contamination | PASS after 0027 for unambiguous rows |
| Production auto-trains multiple pipelines per run | NOT_APPLICABLE / unsupported |

### `DCLAB_PIPELINE_OBSERVABILITY.md`

| Claim | Verdict |
| --- | --- |
| Append-only `ml_run_events` | PASS in PostgreSQL (dedicated test); default pytest DB has no trigger |
| Semantic vs audit purposes disjoint | PASS |
| Observatory tenant 404 | PASS |
| `client_user` cannot use `/business/observatory` | PASS after P0-2 (doc updated) |
| Payload prohibition (secrets, raw rows) | PASS after P1-5 |
| Advisory LLM cannot outrank deterministic verifier | PASS |

---

## Legacy / duplicate / stale classification

| Pattern | Classification |
| --- | --- |
| `client_user` role, JWT, `/app` writes | **Compatibility** — still required by CI crawls and older accounts |
| `users.workspace_id` default workspace | **Compatibility** — selector inference |
| `/admin/organizations` | **Stale IA** beside Workspace-as-Business |
| `/lab/runs/{id}` client checklist | **Stale IA** vs Pipeline Monitor |
| `/admin/models/client-uploads/{id}` | **Stale IA** vs `/admin/pipeline-runs/.../monitor` |
| Five-domain product copy if any remains in marketing pages | **Stale copy** if present; explorer is catalog-driven |
| `Base.metadata.create_all` in pytest | **Test gap** vs production Alembic |
| Duplicate explorer services (platform vs business) | **Intentional split**, not dead code |

---

## Limitations and unsupported functionality

- Automatic multi-pipeline production orchestration (one Labs upload → many `Experiment` rows on one `WorkflowRun`).
- Capability-governed model create/update/delete (no such API).
- Frontend multi-workspace selector via `X-Workspace-Id`.
- Tenant-filtered `/app/insights`.
- Raw observatory event streams filtered by CV/semantic/OpenAI flags.
- Default unit tests exercising PostgreSQL triggers (except the dedicated file).
- LibreOffice visual rendering of DOCX reports.
- CI live OpenAPI crawls were not repeated in this isolated pass.
- Dedicated 500-row timing numbers.

---

## Files changed or created in this verification (working tree vs baseline)

**Modified**

- `alembic.ini` — `path_separator = os`
- `apps/api/app/main.py` — `/business` auth dependencies
- `apps/api/app/api/observability.py`, `business_explorer.py`
- `apps/api/app/services/{lineage,observability,pipeline_verifier,business_explorer,platform_explorer}_service.py`
- Tests: access, business administration, lineage, observability, verifier, platform explorer
- `apps/web/app/admin/businesses/[businessId]/page.tsx`
- `apps/web/app/admin/pipeline-runs/[pipelineId]/monitor/page.tsx`
- `apps/web/package.json`, `package-lock.json`
- `docs/DCLAB_ACCESS_ARCHITECTURE.md`, `docs/DCLAB_PIPELINE_OBSERVABILITY.md`

**Created**

- `apps/api/alembic/versions/0027_repair_legacy_tenant_lineage.py`
- `apps/api/tests/test_legacy_tenant_lineage_migration.py`
- `apps/api/tests/test_ml_run_events_postgres_enforcement.py`
- `apps/web/playwright.config.ts`, `apps/web/e2e/whole-system.spec.ts`
- `scripts/seed_e2e_verification.py`, `scripts/set_e2e_capability.py`
- `docs/verification/BASELINE.md`
- `docs/DCLAB_DATABASE_ARCHITECTURE.md`
- `docs/DCLAB_API_REFERENCE.md`
- `docs/DCLAB_RBAC_CAPABILITY_MATRIX.md`
- `docs/DCLAB_PIPELINE_DEEP_DIVE.md`
- `docs/DCLAB_E2E_VERIFICATION_RUNBOOK.md`
- `docs/DCLAB_VERIFICATION_INDEX.md`
- `docs/DCLAB_SYSTEM_VERIFICATION_REPORT.md` (this file)

Runtime evidence (gitignored): screenshots, Playwright report, `live-openai.json`.

Nothing was committed. The configured development database on port **5432** was not used.

---

## Command log (authoritative reruns)

Isolated cluster assumed already running: `pg_ctl -D artifacts/e2e-verification-pgdata -o "-p 55432 -k /tmp"`.

```text
DATABASE_URL=postgresql://localhost:55432/dclab_e2e_verify .venv/bin/alembic heads
# 0027_repair_tenant_lineage (head)

DATABASE_URL=postgresql://localhost:55432/dclab_e2e_verify .venv/bin/alembic current
# 0027_repair_tenant_lineage (head)

DATABASE_URL=postgresql://localhost:55432/dclab_e2e_verify .venv/bin/alembic check
# No new upgrade operations detected.

DATABASE_URL=postgresql://localhost:55432/decisionai \
MIGRATION_TEST_DATABASE_URL=postgresql://localhost:55432/postgres \
.venv/bin/pytest apps/api/tests -q --tb=line
# 498 passed, 1 skipped, 10 warnings in 232.69s

cd apps/web && npm run lint && npx tsc --noEmit && npm run build
# ESLint clean; tsc clean; Next.js 14.2.18 build clean

cd apps/web && npm run e2e
# 6 passed (1.2m)

.venv/bin/python -m scripts.scan_banned_terms
# Banned-terms scan passed.

.venv/bin/python -m scripts.check_client_lab_schema_contract
# ClientLabUploadSchema ↔ ClientLabUploadRead clean

git diff --check
# clean

# Live OpenAI (key never printed)
.venv/bin/dclab verify-openai-smoke
# plus synthetic Terra audit and request_pipeline_verification routine+deep
# on a completed dclab_e2e_verify upload
```

Playwright fixture password (synthetic only): `VerificationOnly123!`.
