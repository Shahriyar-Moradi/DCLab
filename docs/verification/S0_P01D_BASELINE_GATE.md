# S0-P01D baseline closure gate

**Status:** CURRENT — VERIFIED  
**Plan/prompt:** S0-P01D  
**Verified source SHA:** `91986b9b39bb54c907d50274e94de2febecffaa0`  
**Branch relationship:** `main` = `origin/main`; ahead 0, behind 0  
**Canonical facts:** [truth baseline](../../contracts/truth_baseline.json) and
[truth manifest](../../contracts/truth_manifest.json)  
**Exact-SHA CI:** [GitHub Actions run 34598999220](https://github.com/Shahriyar-Moradi/DCLab/actions/runs/34598999220)

Plan 0.1 is closed for the verified source SHA. The source checkout was clean
before the gate. Local verification used CPython 3.12.13, Node 26.7.0, npm
11.19.0, Playwright 1.62.1, and an isolated PostgreSQL 16.15 cluster on port
55432. GitHub repeated the gate with Python 3.12, Node 20, and PostgreSQL 16.

The evidence update containing this record is an uncommitted documentation and
generated-artifact diff on top of the verified source SHA. It does not change
the product, API, schema, migration graph, runtime configuration, or tests.

## Observed local gate

| Gate / command | Observed result | Duration | Owner |
| --- | --- | ---: | --- |
| `python -m scripts.generate_truth_artifacts --verify-idempotent` | two generations byte-identical; final regeneration produced no new diff | 5.35s | Repository / CI maintainers |
| `git diff --exit-code -- contracts/` on the clean source baseline | clean before this evidence refresh | 0.02s | Repository / CI maintainers |
| `alembic upgrade head` on an empty database | all 58 revisions applied; one head | 2.28s | Backend / data maintainers |
| `python -m scripts.check_truth_drift --alembic-check` | all 13 repository checks plus migrated metadata clean | 9.33s | Repository / CI and backend / data maintainers |
| `python -m scripts.record_repo_truth` | valid deterministic JSON; clean Git relation recorded | 5.67s | Repository / CI maintainers |
| `pytest -q --tb=line` | 1,036 passed, 1 skipped, 20 warnings | 484.80s | Backend / ML platform and SDK maintainers |
| `pytest -q packages/dclab_client/tests` | 8 passed | 0.11s | SDK maintainers |
| banned-terms scan | clean | 5.00s | Product-language / web maintainers |
| object-store tracked-output guard | clean | 0.16s | Storage / repository maintainers |
| Playwright tracked-output guard | clean | 0.07s | Web / repository maintainers |
| Client Labs schema contract | both Pydantic/Zod contracts clean | 0.17s | Backend / web maintainers |
| `make truth-generate` | documented writer worked; expected artifacts only | 4.15s | Repository / CI maintainers |
| `make truth-check` | all 13 read-only checks clean | 5.35s | Repository / CI maintainers |
| `make truth-idempotence` | byte-identical | 4.66s | Repository / CI maintainers |
| `npm ci` | 406 locked packages installed; follow-up `npm audit --omit=dev` found zero production vulnerabilities | 15.32s | Web / dependency maintainers |
| `npx tsc --noEmit` | exit 0 | 2.38s | Web maintainers |
| `npm run lint` | exit 0 with known warnings | 2.23s | Web maintainers |
| `npm run build` | compiled; 31 static pages generated | 19.83s | Web maintainers |
| `npm run e2e` | 18 passed against a freshly recreated database | 91.25s | Web, backend and security maintainers |

The first full pytest attempt is excluded from acceptance evidence. A
concurrent VS Code updater temporarily consumed the host's remaining disk and
caused PostgreSQL `DiskFull` cascades after 579 tests. The updater completed and
self-cleaned; no repository or user artifact was deleted. A newly initialized
cluster then completed the uninterrupted green run above.

## Exact-SHA GitHub agreement

GitHub Actions run 34598999220 was triggered by the push of the verified SHA,
completed on its first attempt, and concluded `success`:

| Job | Result | Started (UTC) | Completed (UTC) | Duration |
| --- | --- | --- | --- | ---: |
| `regression` | success | 2026-09-11 12:27:28 | 2026-09-11 12:43:08 | 15m40s |
| `Whole-system E2E` | success | 2026-09-11 12:43:11 | 2026-09-11 12:47:58 | 4m47s |

The remote regression job applied migrations, regenerated twice with no diff,
ran migrated-metadata truth checks, pytest, language/schema/artifact guards,
the locked frontend install, typecheck, lint, build, and live role-surface
audits. Its backend step took 12m26s. The remote browser step took 2m43s and
passed before Playwright report upload was correctly skipped on success.

## Security, tenancy and adversarial evidence

- No secret, `data/object_store` file, Playwright report, trace, screenshot, or
  test-result path is tracked.
- Production-default secret validation, CURRENT/HISTORICAL contradiction
  checks, broken-link checks, API/SDK drift, migration graph, and generated
  artifact digests all passed.
- The backend suite covered PostgreSQL constraints, cross-workspace denial,
  immutability, concurrency, recovery, and deterministic scientific evidence.
- Browser acceptance covered HttpOnly session continuity, logout/logout-all,
  CSRF denial, role-aware navigation, capability fail-closed behavior,
  read-only developer behavior, and Business A/Business B identifier
  substitution denial.

## Known warnings and ownership

- **Backend / dependency owners:** one Starlette `BlockingPortal` deprecation;
  expected scikit-learn unknown-category transform warnings; SQLAlchemy cannot
  topologically sort the intentional cyclic foreign keys among `datasets`,
  `execution_requests`, `experiments`, and `ingestion_runs`.
- **Web / tooling owners:** `next lint` deprecation, multiple-lockfile workspace
  root inference, and three existing `react-hooks/exhaustive-deps` warnings.
- **Web / dependency owners:** `npm ci` reports deprecated transitive packages,
  install-script review notices, and one high-severity development-dependency
  audit finding. A read-only `npm audit --omit=dev --json` reported zero
  production vulnerabilities. Development dependency upgrades remain a
  separate reviewed tooling/security change.
- **Test-infrastructure owner:** the E2E-only JWT secret is 28 bytes, producing
  a PyJWT length warning. Production fail-closed secret checks passed; changing
  runtime/test configuration is outside this prompt.
- **Tooling owner:** local Node 26 differs from CI's declared Node 20. Both local
  and exact-SHA CI gates passed.
- **Developer environment owner:** `gh` has an invalid saved token. Exact-SHA
  status was verified read-only through GitHub's public Actions API instead.
- The live OpenAI smoke test remains the single intentional pytest skip; no
  external model authority is required by Scope 0 foundation closure.

## Evidence record

```text
Plan/prompt ID: S0-P01D
Claim: Plan 0.1 truth, migration, backend, SDK, web, browser and exact-SHA CI gates agree
Status: VERIFIED
Commit/image digest: 91986b9b39bb54c907d50274e94de2febecffaa0
Environment: local macOS, CPython 3.12.13, Node 26.7.0, npm 11.19.0, PostgreSQL 16.15; GitHub ubuntu-latest, Python 3.12, Node 20, PostgreSQL 16
Migration path: empty database through all 58 revisions to the canonical single head; metadata comparison clean
Commands: truth generator/checker/recorder; Alembic upgrade; full and SDK pytest; static guards; npm ci/type/lint/build/e2e; GitHub Actions run/jobs API
Expected: all gates green; exact-SHA CI success; no tracked runtime outputs; idempotent regeneration; no unexplained diff
Observed: 1,036 passed/1 skipped; SDK 8/8; browser 18/18; all local truth/security/web gates green; exact-SHA CI run 34598999220 success; final two-run regeneration byte-identical
Artifact: docs/verification/S0_P01D_BASELINE_GATE.md and canonical contracts manifest
Security/tenant checks: production-secret, artifact, role, CSRF, capability, cross-workspace, constraints and immutability checks passed
Rollback/kill switch: revert this evidence/current-index refresh and regenerate contracts; no data/schema/runtime rollback or kill switch
Known limitations: warnings listed above; live OpenAI excluded; evidence commit itself requires ordinary CI after commit
Reviewer/date: S0-P01D / 2026-09-11
```

## Next eligible prompt

Plan 0.1 is VERIFIED. The next prompt requiring implementation evidence is
**S0-P02C — persistence, constraints and cleanup reconciliation**.
