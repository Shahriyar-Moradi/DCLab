# ACCESS_MODEL.md

This document is the reference for one rule: **no raw ML-engine output ever reaches a
client-facing screen, API response, or error message.** It describes the role split, the
translation layer that enforces the rule, the automated guardrails that catch a
regression, and — most importantly — what to do when you add a new feature, so this
doesn't silently rot.

If you're adding a client-facing endpoint or page, read the "Adding a new feature"
section at the bottom before you write any code.

---

## 1. The two roles

There are exactly two user roles (`apps/api/app/db/models.py::UserRole`):

| Role | Sees | Sees ML detail? |
|---|---|---|
| `dclab_admin` | Everything under `/admin/*` (API) and `/admin/*` (web) | Yes — full, unrestricted |
| `client_user` | Everything under `/app/*` (API) and `/app/*` (web) | No — translated only |

A user has exactly one role, set at creation (`dclab user create --role dclab_admin\|client_user`).
There is no third role and no "admin who also sees the client view" — an admin uses the
admin surfaces for their own work; the client experience is tested separately, as a
client user.

### How the split is enforced

- **API**: `apps/api/app/main.py` mounts two separate `APIRouter`s — `admin_api` (prefix
  `/admin`, dependency `require_admin`) and `client_api` (prefix `/app`, dependency
  `require_client`). Every route lives under exactly one of these; there is no
  unprefixed, unguarded route. `apps/api/app/api/deps.py` implements `require_admin` /
  `require_client` by decoding the JWT and checking `role`. A client token on any
  `/admin` route gets **403**; no token gets **401**.
- **Web**: `apps/web/middleware.ts` reads the same JWT (from an httpOnly cookie),
  verifies it, and redirects/blocks based on whether the requested path starts with
  `/admin` or `/app`. `apps/web/app/components/layout/SiteHeader.tsx` also only renders
  nav links for the surfaces the current role can reach — but the middleware, not the
  nav, is what actually blocks access.

This is verified exhaustively, not sampled, by:
- `apps/api/tests/test_access_control.py` — walks the **live OpenAPI schema** (not a
  hand-written list) and asserts every single `/admin` route rejects a client token
  (403) and an anonymous caller (401).
- `scripts/audit_admin_surface.py --role client|admin|anonymous` — the same sweep, but
  against a real running server instead of the pytest test client.
- `scripts/audit_web_routes.py` — the same idea for the Next.js pages.

---

## 2. The translation layer

Location: `apps/api/app/translation/`.

The engine (`apps/api/app/engine`, `apps/api/app/sim`) produces raw ML output:
probabilities, model families, feature importances, candidate scores, fusion weights,
metrics like AUC/precision/recall. None of that is a type a client-facing endpoint is
allowed to return. Instead, every client-facing response is built from exactly one
shape:

```python
# apps/api/app/translation/models.py
class ClientFacingInsight(BaseModel):
    subject_id: str          # a customer/lead/opportunity id — never a model id
    category: InsightCategory
    headline: str            # "High retention risk" — never "P(churn) = 0.82"
    confidence_band: ConfidenceBand   # High / Medium / Low — never a raw probability
    recommended_action: str  # "Call the customer" — never a raw action key
    expected_value: float
    currency: str
    reasoning: list[str]     # 2-4 plain sentences — no metrics, no raw scores
    generated_at: datetime
```

There is no field here for a model name, a raw score, or a count of anything the engine
tried internally — the shape itself makes the leak impossible for anything that goes
through it.

### The translators

| File | Translates |
|---|---|
| `bands.py` | A raw probability/agreement score → `ConfidenceBand` (High/Medium/Low). |
| `decisions.py` | The M1 opportunity → decision flow (`translate_opportunity_decision`) — powers `/app/opportunities`, `/app/decisions`. |
| `simulations.py` | All 8 simulation use cases (`translate_simulation_outcome`) — powers `/app/insights` and Client Labs. Each use case has its own reasoning builder because a single generic sentence is either too vague or has to fall back to raw features. |

Every recommended action is humanized through an explicit map
(`ACTION_LABELS` / `ACTION_OVERRIDES`) rather than a raw internal key, specifically
*because* a generic `key.replace("_", " ").capitalize()` can accidentally produce banned
vocabulary — see the `offer_training` → "Offer training" bug in §4.

### `InsightCategory` — business function, not ML task type

Client navigation and the Insights page (`/app/insights`) are organized by business
function, never by ML task type:

`Marketing`, `Sales`, `Revenue`, `Churn & Retention`, `Customer Value`, `Custom`.

---

## 3. The banned-terms list

Single source of truth: `apps/api/app/translation/banned_terms.py`.

**18 words** (word-boundary matched — `_`/`-` count as boundaries too, so
`model_version` and `best-precision` are caught the same as `model` and `precision` on
their own):

`model, ensemble, candidate, auc, roc, precision, recall, calibration, calibrated,
hyperparameter, training, validation, leakage, fusion, robustness, overfit,
overfitting, underfit`

**22 phrases** (substring matched, case-insensitive):

`feature importance, feature_importance, feature group, feature_group, confidence
score, hyper-parameter, best single, best_single, p(y), gradient boosting, random
forest, xgboost, lightgbm, catboost, logistic regression, neural network,
cross-validation, cross validation, held-out, held out, pr_auc, pr-auc`

Extend this list here, not in the scanners below — both scanners import from this one
file.

---

## 4. The guardrails (and what they've actually caught)

Four independent mechanisms, from cheapest/fastest to most expensive/thorough. Any one
of them failing means a banned term or an access-control hole reached a place it
shouldn't have.

### a. Static schema scan — `scan_client_api_response_models()`

Walks every Pydantic `response_model` registered under the `/app` router (recursing into
nested models) and flags any field name, description, or enum value containing a banned
term. Catches structural leaks — e.g. someone adding a field literally called
`model_version` to a client response schema.

### b. Static frontend scan — `scan_frontend_client_tree()`

Greps every `.ts`/`.tsx` file under the client-facing parts of `apps/web` (explicitly
excluding admin pages and public marketing pages, which are allowed to say "model" in a
marketing sense) for banned terms. Catches hardcoded UI copy.

Both (a) and (b) are wrapped by `scripts/scan_banned_terms.py` (`--api-only` /
`--web-only` / no flag = both), and both are proven non-trivial by
`TestScannerCatchesRegressions` in `apps/api/tests/test_translation_layer.py`, which
feeds each scanner a real violation in an isolated fixture and asserts it's caught.

**Live-verified for this document**: a banned term (`AUC`) was actually, temporarily
reintroduced into `apps/web/app/app/insights/page.tsx` and `scripts/scan_banned_terms.py`
was run — it exited non-zero and printed the exact file and term:

```
[FAIL] client frontend source — banned terms found:
  apps/web/app/app/insights/page.tsx: auc
```

The change was reverted and the scan passed clean again. This is not a hypothetical —
the same command is what CI runs on every push.

### c. Live client-surface crawl — `scripts/audit_client_surface.py --role client`

Neither scanner above can catch a banned word that only appears in a **runtime-generated
string** — the schema just says `recommended_action: str`, so a bad value only exists
once the translator actually runs. This script closes that gap: it logs in as a real
client user, calls every one of the 12 `/app/*` operations with real data (uploading a
CSV, generating a decision, running a Client Labs trial, etc.), and scans the actual
response bytes. It also crawls every real `page.tsx` under `apps/web/app/app/`
(discovered from the filesystem, not a hand-list) with a real session cookie.

**This script found a real bug during Step 8**: the churn policy's `offer_training`
action was humanizing to "Offer training" — a legitimate retention action that happens
to collide with the banned word "training." Fixed with an explicit override in
`ACTION_OVERRIDES` (`apps/api/app/translation/simulations.py`), and a regression test
(`test_every_real_policy_action_key_humanizes_clean`) now checks every real action key
from all 8 policy YAMLs, not just a hand-picked safe one.

### d. Live admin-surface crawl — `scripts/audit_admin_surface.py --role client|admin|anonymous`

Reads every `/admin/*` operation from the live OpenAPI schema (so a newly added admin
endpoint is covered the moment it's registered, with no list to update) and asserts a
client token is rejected on 100% of them, an admin token is accepted, and an anonymous
caller is rejected.

### Coverage-drift guard (fast, no live server needed)

`scripts/audit_client_surface.py` exercises a fixed, hand-checked set of operations
(`KNOWN_CLIENT_OPERATIONS`) rather than walking the schema dynamically like (d) does —
because unlike a blanket "reject everything," exercising each `/app` endpoint correctly
requires knowing its specific shape (what ID to substitute, what to upload, etc.).
`apps/api/tests/test_access_control.py::test_client_surface_audit_script_knows_about_every_live_app_operation`
compares that fixed set against the live schema on every normal test run, so adding a
new `/app` endpoint without teaching the crawl script about it fails the fast unit-test
suite immediately — you don't have to wait for the slow live crawl to notice.

---

## 5. CI

`.github/workflows/ci.yml` runs on every push/PR, in this order:

1. Backend test suite (`pytest`) — includes the exhaustive access-control sweep and the
   full translation-layer suite.
2. Static banned-terms scan (`scripts.scan_banned_terms`).
3. Frontend typecheck, lint, build.
4. Boots the real API (`uvicorn`) and the real web app (`next start`, a production
   build — not `next dev`, to avoid React Refresh noise in the crawl).
5. `scripts/audit_admin_surface.py` for all three roles.
6. `scripts/audit_client_surface.py --role client`.

None of this is a script you run manually before a release and hope stays true — every
step above fails the build (non-zero exit) on a violation.

---

## 6. Client Labs — the one place a client triggers a real ML run

`/app/labs` lets a client user run one of a fixed set of pre-defined problems against
sample data or their own CSV upload, and get back translated `ClientFacingInsight`
results — this is DCLab's bounded, free-trial "custom prediction" experience.

**Open ingest (capability 1)** is a separate box at the top of each category. It accepts
a usual data file (spreadsheet, JSON, Parquet, Excel, or a raw log) **without** required
columns. Rows land in `client_lab_uploads`. Disk paths never appear on `/app`. Structuring
messy files (language tools + DCLab's reading pipeline) is capability 2 and is
documented, not built — see `docs/LABS_DATA_UNDERSTANDING.md`.

**Simple-case auto-train** runs automatically, admin-only, behind that same upload when
the file is already structured enough (named columns, spreadsheet/JSON/table file, 40+
rows): EDA, a heuristic target choice, missing-value decisions, a sklearn
`ColumnTransformer`, train/test + K-fold, and RandomForest/XGBoost — persisted as a real
`Experiment`, never a `ClientLabRun`/`ClientLabRunAudit`, and never consuming trial
quota. The client response is unchanged (filename, kind, row count, fields noticed) —
no pipeline field is ever added to `ClientLabUploadRead`. Full detail — EDA, the target
and why it was picked, missing-value log, column roles, candidate scores, and the linked
experiment — is admin-only at `GET /admin/client-uploads/{id}` and summarized on
`/admin/lab`. See `docs/LABS_DATA_UNDERSTANDING.md` for the full pipeline.

Enforced limits (`apps/api/app/services/client_lab_service.py`):

| Limit | Value |
|---|---|
| Max uploaded rows | 500 |
| Max trial runs per problem, per workspace | 3 |
| Wall-clock timeout per run | 30s (soft — the request returns a `failed` status rather than hanging; the orphaned thread is not force-killed) |
| Max insights returned per run | 6 |

**Auditability**: the client only ever sees translated `ClientLabRun.insights`. The full
raw `run_use_case` output is separately persisted as a `ClientLabRunAudit` row, visible
only to admins via the Model Registry (`/admin/models/client-trials/{id}`) and folded
into Monitoring's retrain-event/metric-delta view — so every client-triggered ML run is
fully reviewable with unrestricted detail on the admin side, without ever exposing that
detail to the client who triggered it.

---

## 7. Admin surfaces (full, unrestricted ML detail)

| Surface | Route | Shows |
|---|---|---|
| Organizations | `/admin/organizations` | Workspaces, user counts, opportunity/decision/trial-run counts. |
| Model Registry | `/admin/models` | Every model produced by an experiment, a simulation run, or a client trial — family, fusion, full metrics, candidate count. Client-trial entries link to the raw audit payload. |
| Labs (experimentation) | `/admin/lab/*` | The existing DCLab engine: datasets, tasks, experiments, candidate/ensemble detail (precision, recall, AUC, feature importance — all of it, on purpose). Also lists recent open-ingest uploads and their auto-train `pipeline_status`. |
| Client uploads (auto-train) | `/admin/client-uploads`, `/admin/client-uploads/{id}` | Every open-ingest upload: pipeline status, full EDA/target/missing-value/column-role log, and the linked `Experiment` for simple-case auto-train jobs. |
| Monitoring | `/admin/monitoring` | Retrain events across experiments, simulations, and client trials, with metric deltas between consecutive runs of the same task/use case. |

All of the above require `dclab_admin` and are covered by the same 403/401 sweep as every
other admin route.

---

## 8. Adding a new feature — checklist

If you're adding anything a `client_user` can see or trigger:

1. **Never** return an engine/domain object directly from a `/app` route. Build (or
   reuse) a translator in `app/translation/` that produces a `ClientFacingInsight` (or
   another schema that only contains business-safe fields) and return that.
2. If the action/label text is derived from an internal key (not hand-written), check it
   through `find_banned_terms()` for every real value that key can take — not just one
   "safe" example. (This is exactly the class of bug `offer_training` was: the generic
   humanizer was fine for `send_email` but not for `offer_training`.)
3. Add the new endpoint to `KNOWN_CLIENT_OPERATIONS` in
   `scripts/audit_client_surface.py` and teach the script how to exercise it with real
   data. `test_client_surface_audit_script_knows_about_every_live_app_operation` will
   fail the test suite until you do.
4. If it triggers a real ML run behind the scenes (like Client Labs trials), persist the
   raw output somewhere an admin can review it (see `ClientLabRunAudit`) — never discard
   it, and never expose it on the `/app` side. Open ingest (`POST /app/labs/uploads`)
   still does **not** create a `ClientLabRun` / `ClientLabRunAudit` (those belong to the
   translated trial cards). For simple named-column files it may enqueue an admin-only
   auto-train `Experiment`; persist that on `ClientLabUpload.experiment_id` /
   `pipeline_log`, and never add those fields to `ClientLabUploadRead`.
5. Run `python -m scripts.scan_banned_terms` and `python -m scripts.audit_client_surface --role client`
   locally before opening a PR. CI runs both anyway, but they're fast enough to run
   locally first.

If you're adding anything under `/admin`, the only requirement is: mount it under the
`admin_api` router / `/admin` web path so `require_admin` and the middleware guard it.
Admin content is expected to contain full ML vocabulary — that's the point.
