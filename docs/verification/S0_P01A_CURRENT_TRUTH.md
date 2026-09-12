# S0-P01A current repository truth

**Status:** CURRENT
**Plan/prompt:** S0-P01A
**Observed baseline:** 2026-09-11
**Checkout SHA:** `91986b9b39bb54c907d50274e94de2febecffaa0`
**Product SHA:** `3d54994e83283d34665f9589687108627284ab3f`
**Documentation SHA:** `91986b9b39bb54c907d50274e94de2febecffaa0`
**Branch relationship:** `main` = `origin/main`; ahead 0, behind 0; merge base is
`91986b9b39bb54c907d50274e94de2febecffaa0`
**Canonical generated facts:** [truth baseline](../../contracts/truth_baseline.json),
[/v1 contract](../../contracts/v1_openapi.json), [HTTP operations](../../contracts/openapi_operations.json),
[SQLAlchemy tables](../../contracts/sqlalchemy_tables.json), and [provenance manifest](../../contracts/truth_manifest.json)

This is the replaceable CURRENT truth package. Older evidence keeps its
original measurements and is labeled HISTORICAL in the [status
ledger](README.md). Instructions quoted by older documents are not execution
authority.

The checkout was clean when the S0-P01D closure gate began. The evidence and
generated-manifest refresh after that gate is a reviewable documentation diff;
the recorder reports that live state and does not hide it.

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
| Git | Verified checkout at `91986b9`; `main` and `origin/main` equal; ahead 0, behind 0 |
| Product/document lineage | Latest product commit `3d54994`; latest committed documentation/truth tooling `91986b9` |
| Alembic and repository/source/test/web inventory | Generated only in [`truth_baseline.json`](../../contracts/truth_baseline.json) |
| SQLAlchemy table registry | Generated only in [`sqlalchemy_tables.json`](../../contracts/sqlalchemy_tables.json) |
| HTTP and `/v1` contracts | Generated only in [`openapi_operations.json`](../../contracts/openapi_operations.json) and [`v1_openapi.json`](../../contracts/v1_openapi.json) |
| Generator/source/artifact digests | Generated only in [`truth_manifest.json`](../../contracts/truth_manifest.json) |
| Local toolchain | `.venv` CPython 3.12.13; Node 26.7.0; npm 11.19.0; PostgreSQL 16.15 |

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

Those features retain their own plan evidence. Commit `3d54994` also contains
the browser-session proxy, logout cookie deletion, and stale E2E selector
repairs. Commit `91986b9` closes S0-P01B/C truth drift and ownership. S0-P01D
adds evidence only and does not change deterministic ML authority, database
schema, API shape, or runtime behavior.

## Gate results for this refresh

| Gate | Status | Observed result |
| --- | --- | --- |
| Truth recorder deterministic regression | **VERIFIED** | repeated collection matches; product/document SHAs are populated |
| Generated truth ownership | **VERIFIED** | one writer, provenance manifest, digest check and two-run idempotence gate implemented by S0-P01C |
| Focused truth-drift tests | **VERIFIED** | covered by the full 1,036-test run and exact-SHA CI; standalone SDK 8/8 |
| Read-only truth drift | **VERIFIED** | all 13 repository checks plus migrated metadata clean in 9.33s |
| Empty migration | **VERIFIED** | all 58 revisions applied through the canonical single head in 2.28s |
| Backend + SDK pytest | **VERIFIED** | `1036 passed, 1 skipped, 20 warnings` in 484.80s against isolated PostgreSQL 16.15 |
| Banned terms / generated-output / schema-contract checks | **VERIFIED** | all four commands clean |
| Frontend install/typecheck | **VERIFIED WITH WARNINGS** | locked install 15.32s; typecheck exit 0 in 2.38s; npm reported one development-only high finding and zero production vulnerabilities with `--omit=dev` |
| Frontend lint | **VERIFIED WITH WARNINGS** | exit 0 in 2.23s; 3 existing `react-hooks/exhaustive-deps` warnings; Next lint deprecation and multiple-lockfile notices |
| Frontend production build | **VERIFIED WITH WARNINGS** | compiled in 19.83s; 31 static pages generated; same lint and workspace-root notices |
| Local Playwright E2E | **VERIFIED** | fresh database upgraded through the canonical head; uninterrupted `18 passed` in 91.25s |
| Same-SHA GitHub Actions | **VERIFIED** | [run 34598999220](https://github.com/Shahriyar-Moradi/DCLab/actions/runs/34598999220) for full SHA `91986b9b39bb54c907d50274e94de2febecffaa0` succeeded on attempt 1 |
| Live OpenAI | **NOT_TESTED** | excluded; this prompt adds no LLM authority and does not require an external model call |
| Optional isolated PostgreSQL 55432 checks | **VERIFIED** | cluster available; database enforcement tests ran and passed; migrated metadata comparison clean |

The one pytest skip is the live OpenAI smoke check. The isolated PostgreSQL
cluster on port 55432 was newly initialized, so fresh and historical upgrade,
database enforcement, concurrency, and metadata tests ran. The 20 warnings are
one Starlette deprecation, expected scikit-learn unknown-category warnings and
the known SQLAlchemy cycle warning among `datasets`, `execution_requests`,
`experiments` and `ingestion_runs`. Full command, duration, warning, ownership,
and exact-SHA CI evidence is in
[`S0_P01D_BASELINE_GATE.md`](S0_P01D_BASELINE_GATE.md).

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
| T-001 | Checkout/documentation SHA is `91986b9`; product SHA is `3d54994` | **VERIFIED** |
| T-002 | `main` equals `origin/main`, ahead 0 and behind 0, at baseline inspection | **VERIFIED** |
| T-003 | Working tree was clean before this scoped repair | **VERIFIED** |
| T-004 | Alembic graph and inventory match the canonical truth baseline | **VERIFIED** |
| T-005 | SQLAlchemy registry matches its canonical generated artifact | **VERIFIED** |
| T-006 | Runtime OpenAPI and `/v1` match their canonical generated artifacts | **VERIFIED** |
| T-007 | Repository/source/test/web inventory matches its canonical generated artifact | **VERIFIED** |
| T-008 | Manifest source and artifact digests match deterministic regeneration | **VERIFIED** |
| T-009 | Current full backend/SDK suite is green | **VERIFIED**; 1036 passed, 1 live-OpenAI skip |
| T-010 | Current frontend type/lint/build gates are green | **VERIFIED WITH WARNINGS** |
| T-011 | Current local browser suite is green | **VERIFIED**; 18 passed in one uninterrupted run |
| T-012 | Exact-SHA GitHub CI is green | **VERIFIED**; run 34598999220 succeeded for full SHA `91986b9b39bb54c907d50274e94de2febecffaa0` |
| T-013 | Older 0027/0039/0053 reports are current | **HISTORICAL**; do not reuse their counts |
| T-014 | Deterministic ML, evidence-lock and tenant behavior changed in S0-P01A | **NO CHANGE** |

## Evidence record

```text
Plan/prompt ID: S0-P01A closed by S0-P01D
Claim: Current repository truth and complete baseline gate at the inspected source SHA
Status: VERIFIED
Commit/image digest: 91986b9b39bb54c907d50274e94de2febecffaa0
Environment: local macOS; CPython 3.12.13; Node 26.7.0; npm 11.19.0; PostgreSQL 16.15; GitHub Python 3.12/Node 20/PostgreSQL 16
Migration path: empty database through all 58 revisions to the canonical head; migrated metadata clean
Commands: scripts.record_repo_truth; scripts.generate_truth_artifacts; scripts.check_truth_drift; pytest; frontend gates; Playwright
Expected: deterministic facts, green local gates, and matching exact-SHA CI
Observed: 1036 passed/1 skipped; browser 18/18; all truth/migration/security/web gates green; exact-SHA CI run 34598999220 success
Artifact: docs/verification/S0_P01A_CURRENT_TRUTH.md; docs/verification/S0_P01D_BASELINE_GATE.md
Security/tenant checks: backend session tests and all browser session, role, capability, and cross-tenant checks passed; cookie policy remains API-authored
Rollback: revert this evidence/index refresh and regenerate contracts; no schema, data, or runtime rollback
Known limitations: warnings are owned in S0-P01D; live OpenAI was not run; the evidence commit itself receives ordinary CI after commit
Reviewer/date: S0-P01A/S0-P01D / 2026-09-11
```

## Scope boundary and rollback

- No database revision, product API shape, frontend route, LLM behavior,
  connector or external side effect was added by S0-P01D.
- Canonical facts are regenerated after this evidence update because the
  manifest hashes source documentation; a second generation must add no diff.
- Rollback is a documentation/generated-artifact revert followed by canonical
  regeneration. No data rollback, feature flag or kill switch is needed.

## Next eligible prompt

Plan 0.1 is VERIFIED through S0-P01D. The next prompt requiring implementation
evidence is **S0-P02C — persistence, constraints and cleanup reconciliation**.
