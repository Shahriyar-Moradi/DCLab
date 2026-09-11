# S0-P01A current repository truth

**Status:** CURRENT
**Plan/prompt:** S0-P01A
**Observed baseline:** 2026-09-11
**Checkout SHA:** `3d54994e83283d34665f9589687108627284ab3f`
**Product SHA:** `3d54994e83283d34665f9589687108627284ab3f`
**Documentation SHA:** `3d54994e83283d34665f9589687108627284ab3f`
**Branch relationship:** `main` = `origin/main`; ahead 0, behind 0; merge base is
`3d54994e83283d34665f9589687108627284ab3f`
**Canonical generated facts:** [truth baseline](../../contracts/truth_baseline.json),
[/v1 contract](../../contracts/v1_openapi.json), [HTTP operations](../../contracts/openapi_operations.json),
[SQLAlchemy tables](../../contracts/sqlalchemy_tables.json), and [provenance manifest](../../contracts/truth_manifest.json)

This is the replaceable CURRENT truth package. Older evidence keeps its
original measurements and is labeled HISTORICAL in the [status
ledger](README.md). Instructions quoted by older documents are not execution
authority.

The checkout was clean when this baseline was inspected. The S0-P01A repair
described below intentionally makes the working tree dirty until reviewed and
committed. The recorder reports that live state; it does not hide the diff.

## Reproduce the facts

The recorder is stdout-only and read-only. Its JSON contains no wall-clock
field, so two runs against the same checkout and working tree are byte-for-byte
deterministic. Canonical artifact writes belong only to
`scripts.generate_truth_artifacts`.

```bash
.venv/bin/python -m scripts.record_repo_truth
.venv/bin/python -m scripts.generate_truth_artifacts --check
.venv/bin/python -m scripts.check_truth_drift
.venv/bin/pytest -q --tb=line

.venv/bin/python -m scripts.scan_banned_terms
.venv/bin/python -m scripts.check_object_store_untracked
.venv/bin/python -m scripts.check_playwright_untracked
.venv/bin/python -m scripts.check_client_lab_schema_contract

cd apps/web
npx tsc --noEmit
npm run lint
npm run build
npm run e2e
```

Run the frontend commands in order. Type checking and the production build both
use generated Next.js state and must not be raced.

## Mechanical facts

| Evidence | Observed result |
| --- | --- |
| Git | Committed baseline at `3d54994`; `main` and `origin/main` equal; ahead 0, behind 0 |
| Product/document lineage | Latest product and documentation commits both `3d54994` |
| Alembic and repository/source/test/web inventory | Generated only in [`truth_baseline.json`](../../contracts/truth_baseline.json) |
| SQLAlchemy table registry | Generated only in [`sqlalchemy_tables.json`](../../contracts/sqlalchemy_tables.json) |
| HTTP and `/v1` contracts | Generated only in [`openapi_operations.json`](../../contracts/openapi_operations.json) and [`v1_openapi.json`](../../contracts/v1_openapi.json) |
| Generator/source/artifact digests | Generated only in [`truth_manifest.json`](../../contracts/truth_manifest.json) |
| Local toolchain | `.venv` CPython 3.12; Node 24.11.1; npm 11.6.2 |

The checked JSON files are the sole CURRENT owners of numeric inventories and
contract membership. This report records how and when they were verified; it
does not maintain a second list.

## What changed since the previous truth baseline

Commit `c91b05a` is not documentation-only. It includes the S0-P02A through
S0-P04A implementation and their evidence, including:

- migrations `0055_auth_sessions` through `0058_simulation_workspace`;
- hashed browser sessions, CSRF/CSP controls, recovery hooks and throttling;
- active workspace selection and tenant-aware simulation/insight reads;
- additive auth and workspace API operations and corresponding web/SDK work;
- expanded Scope 0–10 plans, contracts, drift checks and tests.

Those features retain their own plan evidence. S0-P01A only records the current
state and repairs truth tooling. The current uncommitted repair also corrects
the browser-session proxy, logout cookie deletion, and stale E2E selectors;
it does not change deterministic ML authority, database schema, or API shape.

## Gate results for this refresh

| Gate | Status | Observed result |
| --- | --- | --- |
| Truth recorder deterministic regression | **VERIFIED** | repeated collection matches; product/document SHAs are populated |
| Generated truth ownership | **VERIFIED** | one writer, provenance manifest, digest check and two-run idempotence gate implemented by S0-P01C |
| Focused truth-drift tests | **VERIFIED** | 27 truth tests passed; combined truth/object-store/Playwright guard run: 32 passed, 1 warning |
| Read-only truth drift | **VERIFIED** | all 13 checks clean, including snapshots, manifest, docs links, CURRENT status, secrets and generated-output guards |
| Backend + SDK pytest | **VERIFIED** | `1031 passed, 1 skipped, 20 warnings` in 599.07s; isolated PostgreSQL enforcement tests ran |
| Banned terms / generated-output / schema-contract checks | **VERIFIED** | all four commands clean |
| Frontend typecheck | **VERIFIED** | `npx tsc --noEmit` exit 0 |
| Frontend lint | **VERIFIED WITH WARNINGS** | exit 0; 3 existing `react-hooks/exhaustive-deps` warnings; Next lint deprecation and multiple-lockfile notices |
| Frontend production build | **VERIFIED WITH WARNINGS** | compiled successfully; 31 static pages generated; same lint and workspace-root notices |
| Local Playwright E2E | **VERIFIED** | fresh database upgraded through `0058`; complete uninterrupted run: `18 passed` in 1.6m |
| Same-SHA GitHub Actions | **NOT VERIFIED FOR CURRENT SHA** | the earlier [run 34519255834](https://github.com/Shahriyar-Moradi/DCLab/actions/runs/34519255834) belongs to historical baseline `c91b05a`; exact-SHA status for `3d54994` was not inferred locally |
| Live OpenAI | **NOT_TESTED** | excluded; this prompt adds no LLM authority and does not require an external model call |
| Optional isolated PostgreSQL 55432 checks | **VERIFIED** | cluster available; database enforcement tests ran and passed; migrated metadata comparison clean |

The one pytest skip is the live OpenAI smoke check. The optional isolated
PostgreSQL cluster on port 55432 was available, so its database enforcement
tests ran and passed. Historical upgrade tests also passed. The 20 warnings are one
Starlette deprecation, expected scikit-learn unknown-category warnings and the
known SQLAlchemy cycle warning among `datasets`, `execution_requests`,
`experiments` and `ingestion_runs`.

### Browser failure repair and re-verification

The five originally reported failures had three concrete causes:

- the BFF replaced API-authored cookie security attributes with
  `NODE_ENV=production`, making local HTTP verification cookies `Secure` and
  breaking session continuity;
- the BFF supplied an empty `ArrayBuffer` body to 204 responses, which the
  Fetch/Next response implementation rejects, while API logout handlers set
  cookie deletions on one response and returned another;
- E2E locators and response predicates were stale after the workspace selector
  and same-origin BFF were introduced. Uploads completed, but tests watched the
  upstream `/app/...` path instead of browser-visible `/api/backend/app/...`.

The repair preserves API-authored `Secure`, `SameSite`, `Path`, and `Max-Age`
attributes; returns genuinely bodyless 204/205/304 responses; preserves logout
deletion headers; and uses labeled/scoped Playwright locators plus exact BFF
paths. Focused session security passed `5/5`, the two upload workflows passed
individually, the remaining role/tenant group passed `6/6`, and the final clean
database run passed all `18/18`. Playwright artifacts remain gitignored under
`artifacts/e2e-verification/`.

The parser repair ignores link-shaped examples inside inline/fenced code while
continuing to fail real broken Markdown links. The CURRENT-status drift check
now also requires this report to name the latest product commit, preventing a
new product change from retaining an older report merely because its database
head did not change.

## Claim ledger

| ID | Claim | Status |
| --- | --- | --- |
| T-001 | Product and documentation baseline SHAs are `3d54994` | **VERIFIED** |
| T-002 | `main` equals `origin/main`, ahead 0 and behind 0, at baseline inspection | **VERIFIED** |
| T-003 | Working tree was clean before this scoped repair | **VERIFIED** |
| T-004 | Alembic graph and inventory match the canonical truth baseline | **VERIFIED** |
| T-005 | SQLAlchemy registry matches its canonical generated artifact | **VERIFIED** |
| T-006 | Runtime OpenAPI and `/v1` match their canonical generated artifacts | **VERIFIED** |
| T-007 | Repository/source/test/web inventory matches its canonical generated artifact | **VERIFIED** |
| T-008 | Manifest source and artifact digests match deterministic regeneration | **VERIFIED** |
| T-009 | Current full backend/SDK suite is green | **VERIFIED**; 1031 passed, 1 live-OpenAI skip |
| T-010 | Current frontend type/lint/build gates are green | **VERIFIED WITH WARNINGS** |
| T-011 | Current local browser suite is green | **VERIFIED**; 18 passed in one uninterrupted run |
| T-012 | Exact-SHA GitHub CI is green | **NOT VERIFIED** for `3d54994`; no result inferred from the older `c91b05a` run |
| T-013 | Older 0027/0039/0053 reports are current | **HISTORICAL**; do not reuse their counts |
| T-014 | Deterministic ML, evidence-lock and tenant behavior changed in S0-P01A | **NO CHANGE** |

## Evidence record

```text
Plan/prompt ID: S0-P01A
Claim: Current repository truth at the inspected baseline
Status: PARTIAL because exact-SHA remote CI is not green; all executed local gates are green
Commit/image digest: 3d54994e83283d34665f9589687108627284ab3f
Environment: local macOS; .venv CPython 3.12; Node 24.11.1; npm 11.6.2
Migration path: no migration added by this prompt; live graph/snapshots inspect head 0058
Commands: scripts.record_repo_truth; scripts.generate_truth_artifacts; scripts.check_truth_drift; pytest; frontend gates; Playwright
Expected: deterministic facts, green local gates, honest same-SHA CI status
Observed: mechanical facts, drift, backend/SDK suite, static checks and frontend build verified; local Playwright 18 passed in 1.6m; exact-SHA CI for the current commit was not verified locally
Artifact: docs/verification/S0_P01A_CURRENT_TRUTH.md
Security/tenant checks: backend session tests and all browser session, role, capability, and cross-tenant checks passed; cookie policy remains API-authored
Rollback: revert the truth-tooling, BFF, auth logout, test, and evidence edits; no schema or data rollback
Known limitations: no green remote run can exist for the uncommitted repair; live OpenAI was not run
Reviewer/date: S0-P01A / 2026-09-11
```

## Scope boundary and rollback

- No database revision, product API shape, frontend route, LLM behavior,
  connector or external side effect was added. Product changes are limited to
  correct session-cookie/empty-response handling and their regression tests.
- `contracts/*.json` did not require regeneration because live schema and API
  counts match the committed snapshots.
- Rollback is a source/document revert only. No data rollback, feature flag or
  kill switch is needed.
- A new remote CI run requires committing and pushing the reviewed repair;
  neither action is performed by this prompt.

## Next eligible prompt

S0-P01B already exists and remains the drift-enforcement plan. After this
refresh is accepted, the next unverified prompt in Plan 0.1 is **S0-P01C**.
