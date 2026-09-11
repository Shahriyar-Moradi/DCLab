# Verification status ledger

Current mechanical truth lives in
[`S0_P01A_CURRENT_TRUTH.md`](S0_P01A_CURRENT_TRUTH.md).
Canonical head and inventory facts live in
[`contracts/truth_baseline.json`](../../contracts/truth_baseline.json).
Refresh it with:

```bash
.venv/bin/python -m scripts.record_repo_truth
.venv/bin/python -m scripts.generate_truth_artifacts --check
```

The recorder is **read-only** (stdout JSON). It does not migrate databases or
change application files.

Older reports below keep their original evidence. Their executive counts are
**not** current unless the Status column says CURRENT.

| Document | Status | Freeze / SHA | Do not use as current |
| --- | --- | --- | --- |
| [S0_P01A_CURRENT_TRUTH.md](S0_P01A_CURRENT_TRUTH.md) | **CURRENT** | committed baseline `3d54994`; canonical mechanical facts live in `contracts/`; local Playwright 18/18 | Exact-SHA CI for the current commit is not yet verified |
| [S0_P01B_TRUTH_DRIFT.md](S0_P01B_TRUTH_DRIFT.md) | **CURRENT** | Drift CI, snapshots and refresh rules; head is owned by canonical truth | — |
| [S0_P01C_TRUTH_OWNERSHIP.md](S0_P01C_TRUTH_OWNERSHIP.md) | **CURRENT** | One artifact generator, manifest and idempotence gate | — |
| [S0_P02A_BROWSER_SESSIONS.md](S0_P02A_BROWSER_SESSIONS.md) | **CURRENT** | HttpOnly BFF sessions; ADR 0001 | — |
| [S0_P02B_SESSION_HARDENING.md](S0_P02B_SESSION_HARDENING.md) | **CURRENT** | CSRF, CSP, throttle, recovery hooks; ADR 0002 | — |
| [S0_P03A_WORKSPACE_SELECTION.md](S0_P03A_WORKSPACE_SELECTION.md) | **CURRENT** | Active workspace contract and ADR 0003; head is owned by canonical truth | — |
| [S0_P04A_SIMULATION_INSIGHTS.md](S0_P04A_SIMULATION_INSIGHTS.md) | **CURRENT** | SimulationRun/Insights tenancy and ADR 0004; head is owned by canonical truth | — |
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
in `contracts/truth_manifest.json`.
