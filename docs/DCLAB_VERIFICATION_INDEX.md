# DCLab Verification Index

**Current truth:** [`verification/S0_P01A_CURRENT_TRUTH.md`](verification/S0_P01A_CURRENT_TRUTH.md).
Canonical mechanical facts and their provenance are generated in
[`../contracts/`](../contracts/README.md), with head and inventory in
[`truth_baseline.json`](../contracts/truth_baseline.json); this index does not
own their counts.

**Status ledger:** [`verification/README.md`](verification/README.md).

Documents below are **not** silently current. Historical reports keep their
original evidence; do not copy their executive counts into new plans.

| Document | Status | What it is | How it was produced |
| --- | --- | --- | --- |
| [verification/S0_P01A_CURRENT_TRUTH.md](verification/S0_P01A_CURRENT_TRUTH.md) | **CURRENT** | Product/document SHAs, Git relationship, Alembic, OpenAPI, inventories and current gate results | deterministic `scripts/record_repo_truth.py` + local gates + exact-SHA GitHub Actions run 34519255834 (observed failure at repaired link parser) |
| [verification/S0_P02A_BROWSER_SESSIONS.md](verification/S0_P02A_BROWSER_SESSIONS.md) | **CURRENT** | HttpOnly BFF sessions, ADR 0001 | S0-P02A tests + source assertions |
| [verification/S0_P02B_SESSION_HARDENING.md](verification/S0_P02B_SESSION_HARDENING.md) | **CURRENT** | CSRF, CSP, throttle, recovery | S0-P02B tests + Playwright spec |
| [verification/S0_P03A_WORKSPACE_SELECTION.md](verification/S0_P03A_WORKSPACE_SELECTION.md) | **CURRENT** | Active workspace selector and ADR 0003; head is owned by canonical truth | S0-P03A tests |
| [verification/S0_P04A_SIMULATION_INSIGHTS.md](verification/S0_P04A_SIMULATION_INSIGHTS.md) | **CURRENT** | SimulationRun/Insights tenancy and ADR 0004; head is owned by canonical truth | S0-P04A tests |
| [verification/S0_P01B_TRUTH_DRIFT.md](verification/S0_P01B_TRUTH_DRIFT.md) | **CURRENT** | Drift CI, synthetic-fail tests, snapshot refresh | `scripts/check_truth_drift.py` + `contracts/` |
| [verification/S0_P01C_TRUTH_OWNERSHIP.md](verification/S0_P01C_TRUTH_OWNERSHIP.md) | **CURRENT** | Single generator, artifact ownership, provenance manifest, idempotence | `scripts/generate_truth_artifacts.py` + `contracts/truth_manifest.json` |
| [../contracts/README.md](../contracts/README.md) | **CURRENT** | Intentional contract change vs accidental drift | S0-P01B |
| [verification/README.md](verification/README.md) | **CURRENT** | Current vs historical status ledger | S0-P01A |
| [verification/BASELINE.md](verification/BASELINE.md) | **HISTORICAL** | Pre-repair `main` SHA `de2af56`, tool versions, isolation policy | Recorded 2026-09-04 before repairs |
| [DCLAB_DATABASE_ARCHITECTURE.md](DCLAB_DATABASE_ARCHITECTURE.md) | **HISTORICAL freeze (0053)** | Physical schema, FKs, indexes, JSONB risks at `0053_pipeline_run_branch` | Alembic head + `models.py` + pg_catalog; use canonical truth for the current head |
| [DCLAB_DATABASE_MIGRATION_MAP.md](DCLAB_DATABASE_MIGRATION_MAP.md) | **HISTORICAL map** | Redesign object map through 0053; 0054 addendum at top | Its addendum is superseded; use canonical truth for the current head |
| [DCLAB_API_REFERENCE.md](DCLAB_API_REFERENCE.md) | **HISTORICAL** | Runtime OpenAPI operations (then 94) | FastAPI `app.openapi()` at an older SHA; use canonical truth for current operations |
| [DCLAB_RBAC_CAPABILITY_MATRIX.md](DCLAB_RBAC_CAPABILITY_MATRIX.md) | **LIVING source** | Role/capability enforcement | Route guards + capability service + tests |
| [DCLAB_PIPELINE_DEEP_DIVE.md](DCLAB_PIPELINE_DEEP_DIVE.md) | **LIVING source** | Implementation-linked pipeline topics | `auto_train_service`, `runner`, `auto_prepare`, verifier |
| [DCLAB_E2E_VERIFICATION_RUNBOOK.md](DCLAB_E2E_VERIFICATION_RUNBOOK.md) | **LIVING runbook** | Optional local 55432 commands | Required browser E2E is GitHub `Whole-system E2E` on the same SHA |
| [DCLAB_SYSTEM_VERIFICATION_REPORT.md](DCLAB_SYSTEM_VERIFICATION_REPORT.md) | **HISTORICAL** | Claim ledger, P0–P3, checklists, command log at Alembic **0027** | Isolated 55432 + Playwright + live OpenAI on 2026-09-04 |
| [DCLAB_ACCESS_ARCHITECTURE.md](DCLAB_ACCESS_ARCHITECTURE.md) | **LIVING source** | Identity and route trees | Corrected where runtime disproved a claim |
| [DCLAB_BUSINESS_ADMINISTRATION.md](DCLAB_BUSINESS_ADMINISTRATION.md) | **LIVING source** | Business plane | Source doc; see current tests, not 0027 counts |
| [DCLAB_PLATFORM_ADMINISTRATION.md](DCLAB_PLATFORM_ADMINISTRATION.md) | **LIVING source** | Platform plane | Source doc |
| [DCLAB_DATA_AND_MODEL_LINEAGE.md](DCLAB_DATA_AND_MODEL_LINEAGE.md) | **PARTIAL / older diagram** | Lineage hierarchy before DataAccess was first-class | Canonical chain is now Workspace → Project → ProblemSpec → DataSource → DataAccess → IngestionRun → Dataset → WorkflowRun → PipelineRun → ModelVersion |
| [DCLAB_PIPELINE_OBSERVABILITY.md](DCLAB_PIPELINE_OBSERVABILITY.md) | **LIVING source** | Events and LLM ledger | Observatory role gate in tests |
| [DCLAB_ADAPTIVE_MODEL_BUILDER.md](DCLAB_ADAPTIVE_MODEL_BUILDER.md) | **LIVING source** | Phase 1 scientific planning layer plus adaptive final holdout | ProblemProfile, HoldoutPlan, ValidationPlan, MetricPlan, LeakageAuditor, verifier, benchmarks |
| [DCLAB_ADAPTIVE_MODEL_BUILDER_CORRECTNESS.md](DCLAB_ADAPTIVE_MODEL_BUILDER_CORRECTNESS.md) | **LIVING source** | Production Labs E2E scientific proof (Repairs 1–3) | `/app/labs/uploads` → `run_auto_train_job` → ModelVersion → verifier → monitor |
| [DCLAB_DATABASE_ERD.md](DCLAB_DATABASE_ERD.md) | **HISTORICAL** | Mermaid ERD | Frozen at Alembic **0039**; use canonical truth for the current head |

Evidence artifacts (gitignored): `artifacts/e2e-verification/`.
