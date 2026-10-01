# Verification status ledger

Current mechanical truth lives in
[`S0_P01A_CURRENT_TRUTH.md`](S0_P01A_CURRENT_TRUTH.md).
Canonical head and inventory facts live in
[`contracts/truth_baseline.json`](../../contracts/truth_baseline.json).
Refresh it with:

```bash
.venv/bin/python -m scripts.generate_truth_artifacts
.venv/bin/python -m scripts.generate_truth_artifacts --check
.venv/bin/python -m scripts.generate_truth_artifacts --verify-idempotent
```

Use `.venv/bin/python -m scripts.record_repo_truth` for a **read-only** JSON
report. It does not migrate databases or change application files.

Older reports below keep their original evidence. Their executive counts are
**not** current unless the Status column says CURRENT.

| Document | Status | Freeze / SHA | Do not use as current |
| --- | --- | --- | --- |
| [S0_P01A_CURRENT_TRUTH.md](S0_P01A_CURRENT_TRUTH.md) | **CURRENT — VERIFIED** | verified source `91986b9`; canonical mechanical facts live in `contracts/`; local Playwright 18/18 | Live OpenAI remains excluded |
| [S0_P01B_TRUTH_DRIFT.md](S0_P01B_TRUTH_DRIFT.md) | **CURRENT** | Drift CI, snapshots and refresh rules; head is owned by canonical truth | — |
| [S0_P01C_TRUTH_OWNERSHIP.md](S0_P01C_TRUTH_OWNERSHIP.md) | **CURRENT** | One artifact generator, manifest and idempotence gate | — |
| [S0_P01D_BASELINE_GATE.md](S0_P01D_BASELINE_GATE.md) | **CURRENT — VERIFIED** | complete local gate plus exact-SHA [CI run 34598999220](https://github.com/Shahriyar-Moradi/DCLab/actions/runs/34598999220) | Warnings and owners are recorded in the evidence |
| [S0_P02A_BROWSER_SESSIONS.md](S0_P02A_BROWSER_SESSIONS.md) | **CURRENT** | HttpOnly BFF sessions; ADR 0001 | — |
| [S0_P02B_SESSION_HARDENING.md](S0_P02B_SESSION_HARDENING.md) | **CURRENT** | CSRF, CSP, throttle, recovery hooks; ADR 0002 | — |
| [S0_P02C_SESSION_PERSISTENCE.md](S0_P02C_SESSION_PERSISTENCE.md) | **CURRENT** | Session/recovery constraints, lineage, bounded cleanup | — |
| [S0_P02D_BROWSER_BFF.md](S0_P02D_BROWSER_BFF.md) | **CURRENT** | BFF contract: request-id, cache-control, streaming bounds, 502 | — |
| [S0_P02E_SESSION_OPERATIONS.md](S0_P02E_SESSION_OPERATIONS.md) | **CURRENT** | Typed auth settings, kill switch, metrics, incident runbooks | — |
| [S0_P02F_ADVERSARIAL_GATE.md](S0_P02F_ADVERSARIAL_GATE.md) | **CURRENT** | Two-user session matrix, bearer without cookies, production-shaped kill switch | — |
| [S0_P03A_WORKSPACE_SELECTION.md](S0_P03A_WORKSPACE_SELECTION.md) | **CURRENT** | Active workspace contract and ADR 0003; head is owned by canonical truth | — |
| [S0_P03B_CAPABILITY_AUTHORITY.md](S0_P03B_CAPABILITY_AUTHORITY.md) | **CURRENT** | Versioned server capability matrix, bounded invalidation and direct API denial; local verification | Exact-SHA CI pending |
| [S0_P03C_TENANT_FRONTEND_STATE.md](S0_P03C_TENANT_FRONTEND_STATE.md) | **CURRENT** | Tenant-keyed browser state and safe switch; local browser race test | Exact-SHA CI pending |
| [S0_P03D_WORKSPACE_PROPAGATION.md](S0_P03D_WORKSPACE_PROPAGATION.md) | **CURRENT** | Explicit bearer scope, browser request snapshot, path/header agreement and BFF spoof test | Exact-SHA CI pending |
| [S0_P03E_ISOLATION_GATE.md](S0_P03E_ISOLATION_GATE.md) | **CURRENT — locally verified** | Live tenant-route inventory, two-workspace PostgreSQL matrix, browser switch/spoof gate and invalidation runbook | Exact-SHA CI pending |
| [S0_P04A_SIMULATION_INSIGHTS.md](S0_P04A_SIMULATION_INSIGHTS.md) | **CURRENT** | SimulationRun/Insights tenancy and ADR 0004; head is owned by canonical truth | — |
| [S0_P04B_TENANT_LINEAGE.md](S0_P04B_TENANT_LINEAGE.md) | **CURRENT — locally verified** | Simulation/Insights and admin derivative workspace filtering, previous-head migration, audit and concurrency gate | Exact-SHA CI pending |
| [S0_P04C_AUDIENCE_CLOSURE.md](S0_P04C_AUDIENCE_CLOSURE.md) | **CURRENT — locally verified** | Workspace/capability-first event reads, legacy diagnostic projections, SDK/artifact locator closure | Exact-SHA CI pending |
| [S0_P04D_RETIREMENT_ISOLATION_GATE.md](S0_P04D_RETIREMENT_ISOLATION_GATE.md) | **CURRENT — locally verified** | 1,118 backend/SDK pass, 27 browser pass; two-workspace Insights BFF, archive quarantine and forward-repair runbook | Exact-SHA CI pending; Plan 0.4 not formally closed |
| [S0_P05A_POLICY_BOOTSTRAP.md](S0_P05A_POLICY_BOOTSTRAP.md) | **HISTORICAL — locally verified at 0060** | Versioned tenant-bound dataset defaults; null column labels deny; 1,122 backend/SDK tests passed, migration rollback/reapply checked | Exact-SHA CI/review pending; Plan 0.5 not closed |
| [S0_P05B_QUARANTINE_PUBLICATION.md](S0_P05B_QUARANTINE_PUBLICATION.md) | **PARTIAL** | Additive publication lineage and production fail-closed upload guard; classification/publish flow awaits a product decision | No full gate or exact-SHA CI |
| [../../contracts/README.md](../../contracts/README.md) | **CURRENT** | How to refresh snapshots vs breaking API changes | — |
| [BASELINE.md](BASELINE.md) | **HISTORICAL** | `de2af56`, 2026-09-04 pre-repair | SHA, Python 3.14 host note as “the” baseline |
| [../DCLAB_SYSTEM_VERIFICATION_REPORT.md](../DCLAB_SYSTEM_VERIFICATION_REPORT.md) | **HISTORICAL** | Alembic **0027**, 498 pytest, 6 Playwright, 29 routes | Any executive count |
| [../DCLAB_DATABASE_ARCHITECTURE.md](../DCLAB_DATABASE_ARCHITECTURE.md) | **HISTORICAL freeze** | Alembic **0053** catalog (2026-09-09) | Calling 0053 “head” |
| [../DCLAB_DATABASE_MIGRATION_MAP.md](../DCLAB_DATABASE_MIGRATION_MAP.md) | **HISTORICAL map + 0054 addendum** | Redesign spine through 0053; addendum is superseded | Treating its head as current instead of using canonical truth |
| [../DCLAB_DATABASE_ERD.md](../DCLAB_DATABASE_ERD.md) | **HISTORICAL** | Logical model at **0039** | Treating 0039 as head |
| [../DCLAB_API_REFERENCE.md](../DCLAB_API_REFERENCE.md) | **HISTORICAL** | 94 OpenAPI operations | Operation count |
| [../DCLAB_E2E_VERIFICATION_RUNBOOK.md](../DCLAB_E2E_VERIFICATION_RUNBOOK.md) | **LIVING runbook, HISTORICAL port default** | Local 55432 optional; required E2E is GitHub CI | “Must use 55432” as the only E2E |

S0-P01B fails CI when CURRENT claims or canonical facts drift. S0-P01C assigns
each fact family to one artifact and records generator/source/artifact digests
in `contracts/truth_manifest.json`. S0-P01D closes Plan 0.1 with a clean local
gate and successful exact-SHA CI.
