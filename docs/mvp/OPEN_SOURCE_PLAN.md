# Open-source plan (P8.6-A)

**Status:** plan only. Nothing is published, no file was moved, and no license is
chosen: the founder decides in [ADR 0011](../adr/0011-open-source-license.md)
(Proposed). Written 2026-10-16 against `dev` at `db74d6d`.

## 1. What would be published

| Package | Source today | State after P8.6-A |
| --- | --- | --- |
| `dclab-client` (SDK + `dclab-cli`) | `packages/dclab_client` | Builds a wheel offline, installs into a fresh venv, `import` and `--help` work (`python -m scripts.check_packages_build`). Honest metadata, no license claim. |
| `dclab-mcp` (stdio MCP server) | `packages/dclab_mcp` | Same check. `mcp>=2.2.0,<2.3` (the repo lock still installs exactly 2.2.0); `--help`/`--version` added. |
| `dclab-engine` (pure ML) | `apps/api/app/engine` (82 modules, ~18.9k lines) | **Not packaged.** The import boundary is enforced (§3); the move is planned (§4). |

Both existing packages carry the classifier `Private :: Do Not Upload`, which makes
PyPI refuse an accidental upload. It is removed only together with the license
metadata (checklist, §6).

**Engine third-party dependencies** (from `python -m scripts.engine_boundary --report`):
numpy, pandas, scikit-learn, scipy, pyarrow, joblib, pydantic, skops; optional
extras: xgboost, lightgbm, catboost (boosting) and optuna (tuning).

## 2. What stays private

Everything that holds tenancy, authority or the business: `app/api`, `app/services`,
`app/db` and `alembic/` (schema, workspace tenancy), `app/middleware`, `app/cli`,
`app/storage`, `app/sim` and `app/ml` (frozen legacy), `app/agents` (NOOA, Jev, AI
gateway, governance, prompt registry), the `app/domain` read models,
`apps/web`, infrastructure, `demo/`, `GPT_Demo/`, `data/` and every business or
customer document (`docs/archive/` holds business proposals that name people;
`docs/mvp/STATUS.md`, `ROADMAP.md` and the verification evidence record internal
plans and founder decisions).

## 3. Engine boundary (enforced now)

`apps/api/tests/test_engine_import_boundary.py` (no database; reads source with `ast`,
including function-scope, relative and `importlib.import_module("...")` imports) fails when:

- engine code imports `app.api`, `app.cli`, `app.db`, `app.main`, `app.middleware`,
  `app.ml`, `app.services`, `app.sim`, `app.storage`, or `sqlalchemy`, `fastapi`,
  `starlette`, `alembic`, `psycopg`, `uvicorn`, `openai` (no allowlist possible);
- any other non-engine `app.*` import has no entry in `ALLOWLIST`
  (`scripts/engine_boundary.py`, at most 8 entries, each with a reason and an owner);
- an allowlist entry is stale.

The `ast` test was chosen over `import-linter`: it needs no new dependency or lock
change, runs offline, and also catches function-scope imports and stale entries.

**Measured before P8.6-A:** 24 import statements in 15 engine files reached non-engine
`app.*` code; none reached the forbidden list directly. **Fixed (moved into the engine;
the domain re-exports, values unchanged):** runner stage keys →
`engine/experiments/stages.py`; scoring failure codes and the contract-check bound →
`engine/serving/codes.py`; the Olist adapter resolves its data root itself instead of
importing `app.config`. **Remaining (allowlisted):** 20 statements, 8 entries:

| Engine module | Imports | Plan |
| --- | --- | --- |
| `lab/llm_client`, `lab/prompts/*` | `app.agents.*`, `app.config` | LLM island: leaves the engine (step 1) |
| `investigate/*` | `app.domain.findings` | move the vocabulary into the engine (step 2) |
| `lab/column_map` | `app.domain.lab_use_cases` | move the catalog into the engine (step 2) |
| `lab/open_ingest` | `app.translation.banned_terms` | pass a sanitizer in, or keep private (step 2) |
| `serving/artifacts`, `serving/package_loader` | `app.config` (function scope) | scratch root and the model-load security policy become parameters (step 2) |

**Import-time reach** (the closure through engine imports and parent packages):
18 of 82 modules cannot be imported without non-engine code today — `investigate/*`,
`lab/column_map`, `lab/open_ingest`, and the LLM island `lab/llm_client`,
`lab/decision_validator`, `lab/prompts/*`. Through the island they reach
`app.agents.prompt_releases`, which imports `app.db.models` and SQLAlchemy. The
training core (`experiments/runner`, `modeling/*`, `search`, `validation`, `features`,
`serving/batch_scoring`) imports cleanly; it reaches the island only through
function-scope imports (`modeling/leakage_auditor.consult_leakage_llm` and its lazy
`decision_validator` call).

## 4. Extraction approach

| Option | For | Against |
| --- | --- | --- |
| A. Build step: `packages/dclab_engine` copies `apps/api/app/engine` at build time and rewrites `app.engine` → `dclab_engine` | no churn in the private repo | the published source is not the reviewed source; tracebacks and line numbers differ; a second, generated owner of the code |
| B. Keep `app.engine` as the published import name | no rewrite | `app` is a generic top-level name that collides in users' environments |
| **C. One physical move (recommended)** to `packages/dclab_engine/src/dclab_engine/` with a codemod `app.engine` → `dclab_engine`, done in one PR when no engine prompt is in flight; the server then depends on it as a path dependency (pytest `pythonpath` already lists `packages/*`; the API/worker images add an editable install next to their `pip install -e .`) | one owner, the published code is the tested code, src layout prevents accidental repo imports | one large mechanical diff (services, tests, docs, `.claude/rules` paths); needs a quiet window |

**Recommendation: C**, after steps 1–2 bring the allowlist to zero for the published
modules. Doing the move before that would publish a package that imports `app.*`.

## 5. Steps to a clean public repo

1. **Remove the LLM island from the engine:** `lab/llm_client`, `lab/decision_validator`,
   `lab/prompts/*` and `consult_leakage_llm` move to `app/agents` (or a services
   adapter); the engine keeps only the injected `LeakageReviewer` callable it already has.
2. **Zero the allowlist:** move the findings vocabulary and the use-case catalog into the
   engine; turn the `app.config` reads into parameters (scratch root, a model-load policy
   object that keeps the worker-only and legacy-pickle gates); give `open_ingest` a
   sanitizer parameter or keep it private. The test then has an empty allowlist.
3. **Move** (option C), add `packages/dclab_engine/pyproject.toml`, extend
   `scripts/check_packages_build.py` to a third wheel; split engine tests into pure ones
   (published) and server ones (`tests/engine/test_lab_api.py`,
   `test_feature_contract.py`, `test_scientific_candidate_fingerprint.py` use server code).
4. **License:** founder accepts ADR 0011; add `LICENSE`, `License-Expression` and the
   contributor terms (DCO or CLA) the ADR picks.
5. **Fresh history:** create the public repository from an export of only the three
   packages, their tests and public docs, as a new initial commit. Never publish or
   `filter-repo` this repository: its 235-commit history contains the `docs/archive/`
   business proposals (which name people) and internal plans.
6. **Exclude:** `docs/archive/`, `docs/mvp/` (except a rewritten engine guide),
   `docs/verification/`, `demo/`, `GPT_Demo/`, `data/`, `.claude/`, infrastructure,
   `apps/web`, everything under §2.
7. **Secrets scan:** run a scanner (gitleaks or trufflehog; none is wired today) on the
   export tree before the first push, and once on this repo's full history so anything
   found is rotated here too. Test fixtures use fake `dclab_st_...` tokens; allowlist
   them by path in the scanner config, never by pattern.
8. **Legacy archive:** the frozen legacy surfaces (Decision.ai simulation, insights,
   opportunities; [S0-P04D gate](../verification/S0_P04D_RETIREMENT_ISOLATION_GATE.md))
   must be removed or formally re-homed by **2027-03-31**. None of it may enter a public
   package; `app.ml` and `app.sim` are on the engine's forbidden list for that reason.
9. **Publish:** remove `Private :: Do Not Upload`, add project URLs, release from the
   public repo's CI with PyPI trusted publishing (no long-lived upload token).

## 6. Checklist

- [x] Engine import boundary enforced by a test; allowlist ≤ 8 entries, each justified
- [x] Planted violations fail the test (test fixtures + a manual plant in a real engine file)
- [x] `dclab-client` and `dclab-mcp` build, install in a fresh venv, import, `--help`
- [x] No license claim anywhere; accidental-upload guard on both packages
- [x] License options recorded (ADR 0011, Proposed)
- [ ] Founder accepts a license (ADR 0011)
- [ ] LLM island out of the engine (step 1)
- [ ] Allowlist at zero for published modules (step 2)
- [ ] Engine moved to `packages/dclab_engine` and built as a wheel (step 3)
- [ ] Offline install with dependencies resolved from an index: today the local uv
      cache cannot resolve `mcp`'s dependency tree offline, so the check installs the
      wheels `--no-deps` and borrows the dev environment after verifying the declared
      ranges admit it; a networked CI run must resolve them for real
- [ ] Secrets scan of the export and of this repo's history
- [ ] Public repo with fresh history, CONTRIBUTING, SECURITY, CODE_OF_CONDUCT
- [ ] Trusted publishing configured; upload guard removed
