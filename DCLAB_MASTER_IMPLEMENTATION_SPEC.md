
# DCLab Master Product and Implementation Specification

**Repository:** `Shahriyar-Moradi/DCLab`  
**Purpose:** Single source of truth for Codex/Coding Agent implementation  
**Current phase:** General-purpose deterministic tabular ML platform with selective small-LLM assistance for ambiguity  
**Future autonomous-agent/critic/experiment-loop work:** Explicitly paused until the deterministic workflow is fully verified

---

# 1. Product Idea

DCLab is not a Telco churn application and is not a collection of hardcoded business models.

The core product is a **general-purpose data intelligence and machine-learning platform**.

In the current phase, a normal user or an admin uploads a tabular data file. DCLab should understand the dataset, prepare it, build and validate machine-learning models, create predictions, persist everything, and show the correct result to the correct audience.

The high-level product flow is:

```text
ANY TABULAR DATASET
        ↓
INGEST
        ↓
UNDERSTAND
        ↓
ANALYZE
        ↓
CLEAN
        ↓
PREPARE / ENGINEER FEATURES
        ↓
TRAIN / VALIDATE MODELS
        ↓
SELECT BEST VALIDATED MODEL
        ↓
PREDICT ON UNTOUCHED TEST DATA
        ↓
PERSIST RESULTS
        ↓
CLIENT RESULT + ADMIN TECHNICAL VIEW
```

The client experiences a simple product.

The backend performs a rigorous ML workflow.

The admin can inspect the important technical evidence.

---

# 2. Business Core

The business value is not "upload a Telco CSV and run XGBoost."

The business value is:

> A user can provide an unfamiliar business or operational dataset and DCLab can turn it into a valid, traceable predictive ML result with minimal manual data-science work.

Examples of valid future/current tabular use cases include:

- customer churn
- fraud detection
- loan/default prediction
- conversion prediction
- marketing response
- sales prediction
- revenue regression
- demand prediction
- pricing
- manufacturing quality
- sensor/operations data
- customer value
- healthcare tabular prediction
- arbitrary classification
- arbitrary regression

The core engine must not depend on the names `Churn`, `TotalCharges`, `tenure`, `MonthlyCharges`, `Contract`, `PaymentMethod`, or any other specific business schema.

Domain-specific datasets are tests/examples, not architecture.

---

# 3. Current Phase Boundary

Do not build the autonomous multi-agent architecture yet.

The current phase is:

```text
Deterministic ML Engine
        +
Small LLM Semantic Assistance
        +
Client/Admin Product Workflow
```

The future ideas below are intentionally paused:

- autonomous orchestrator
- ML Critic
- self-directed experiment graph loop
- iterative hypothesis generation
- autonomous feature-research loop
- budget-controlled agent loop

Those should not start until the deterministic end-to-end verification suite is green.

---

# 4. User Types

## 4.1 Client

The client should see a minimal workflow:

```text
UPLOAD
  ↓
PROCESSING
  ↓
RESULT
  ↓
PREDICTIONS
```

The client should not see the internal ML procedure.

Do not expose by default:

- EDA internals
- cleaning rule names
- `SimpleImputer`
- `StandardScaler`
- `OneHotEncoder`
- `ColumnTransformer`
- train/test internals
- CV folds
- candidate models
- feature engineering internals
- raw pipeline logs
- technical decision ledgers

The client sees the actual outcome.

Example:

```text
Analysis Complete

Dataset: customer_data.csv
Records analyzed: 7,043
Prediction target: Churn

Model performance:
ROC-AUC 0.856

Predictions generated:
1,409

[View Predictions]
[Download Results]
```

All values must come from persisted backend results.

No fake metrics.

No mocked results.

---

## 4.2 Admin

The admin sees the important technical workflow.

Admin Lab should make it possible to answer:

1. What dataset was processed?
2. What target and task were selected?
3. What data-quality problems existed?
4. What was done to each affected column?
5. Why was that action taken?
6. What features were retained/generated/removed?
7. How was validation performed?
8. Which models were compared?
9. What were their CV/test results?
10. Which model was selected?
11. Why was it selected?
12. How many predictions were created?
13. Can the prediction set be downloaded?
14. Was a decision produced by deterministic rules, an LLM override, or fallback?

Admin navigation should eventually be coherent around:

```text
Lab
├── Overview
├── Datasets
├── Runs
├── Experiments
├── Models
└── Predictions
```

---

# 5. Canonical Data Flow

The target flow is:

```text
User/Admin
   ↓
Upload file
   ↓
Persist original upload
   ↓
Create canonical run
   ↓
Start backend processing
   ↓
Return run_id
   ↓
Frontend navigates to /lab/runs/{run_id}
   ↓
Backend continues processing
   ↓
Frontend reads real persisted run state
   ↓
Completed result
   ↓
Predictions
```

The backend/database is always the source of truth.

Frontend state must not be the authoritative run state.

Refresh and direct URL access must work.

---

# 6. Supported Input Philosophy

The current priority is structured or recoverably structured tabular data.

Examples:

- CSV
- TSV
- Excel
- Parquet
- JSON records
- JSONL/NDJSON
- other simple table files when existing ingestion supports them

The system should not assume that every uploaded file has a perfect schema.

Cases include:

### Case A — clean named columns

```text
age,income,region,defaulted
```

Proceed normally.

### Case B — numeric data stored as strings

```text
TotalCharges = "1234.50"
```

Detect high numeric parse rate and coerce safely.

### Case C — missing values

Detect and handle with deterministic rules, with selective LLM assistance only when ambiguous.

### Case D — ambiguous column type

Example: integer codes may actually be categories.

Deterministic evidence is generated first. A small LLM may assist when ambiguity is real.

### Case E — no obvious target

Do not silently assume the last column.

Use deterministic target-candidate generation plus semantic interpretation.

If confidence remains insufficient, fail safely or require explicit target selection.

### Case F — incomplete/unusable schema

Use profiling and semantic interpretation where possible.

Do not hallucinate missing columns or invent data.

---

# 7. General-Purpose Dataset Understanding

This is a core requirement.

DCLab must analyze the actual uploaded dataset rather than mapping it into a small list of hardcoded business use cases.

The deterministic profile should calculate:

## Dataset-level

- row count
- column count
- column names
- memory usage where useful
- duplicate count
- total missing cells
- missing percentage

## Per column

- name
- dtype
- missing count
- missing ratio
- unique count
- unique ratio/cardinality
- constant / near constant
- high cardinality
- likely identifier
- probable datetime
- representative values
- numeric statistics when numeric
- categorical distribution when categorical

## Numeric

- min
- max
- mean
- median
- standard deviation
- quantiles
- skewness when implemented

## Categorical

- cardinality
- top values
- frequencies

## Potential semantic roles

- numerical
- categorical
- boolean
- datetime
- identifier
- text/free-text
- target candidate

The profiler must be deterministic and domain-independent.

---

# 8. Small LLM Role

Use a small configurable LLM only for semantic ambiguity.

Possible models may change. The architecture must use configuration, not hardcode a specific model name.

The LLM is not the ML engine.

The LLM must not calculate statistics that Python can calculate.

The default architecture is:

```text
Raw file
  ↓
Deterministic profiler
  ↓
Compact structured evidence
  ↓
Small LLM
  ↓
Schema-validated interpretation
  ↓
Deterministic validator
  ↓
Accepted decision or fallback
```

## Valid LLM responsibilities

- interpret ambiguous column semantics
- distinguish numerical code vs categorical code
- identify likely identifiers
- rank target candidates
- infer classification vs regression when ambiguous
- interpret ambiguous missing-value situations
- explain a proposed decision
- possibly identify obvious semantic relationships from column metadata

## Invalid LLM responsibilities

- train models
- calculate distributions
- calculate missing ratios
- fit imputers
- fit scalers
- split data
- run CV
- execute pandas arbitrarily
- override deterministic safety checks
- invent columns
- invent target labels
- receive millions of raw rows by default

## LLM output requirements

- strict JSON schema
- confidence score
- rationale
- referenced evidence
- only real existing columns
- validator gate before application
- deterministic fallback if unavailable

The entire pipeline must still function when the LLM is disabled/unavailable, except for cases that genuinely cannot be resolved safely.

---

# 9. Target and Task Understanding

This is one of the largest required generalization changes.

## Priority

1. Explicit user/admin target selection, when provided
2. Deterministic target candidate generation
3. Small-LLM semantic ranking
4. Validation
5. Safe failure if insufficient confidence

Never default to "last column."

Never assume `Churn`.

Never require one of five predefined business label aliases.

## Target candidate evidence

Possible evidence:

- label-like name
- low/appropriate cardinality
- binary categorical distribution
- continuous numeric outcome characteristics
- identifier rejection
- temporal leakage risk
- semantic meaning from column name/sample values

## Task type

At minimum:

```text
binary classification
multiclass classification
regression
```

Current minimum required finish line can remain binary classification + regression if multiclass is not yet implemented, but architecture must not assume all classification is Telco/binary.

---

# 10. Data Cleaning

Cleaning runs automatically under the hood.

Examples:

- replace infinities with missing
- normalize recognized missing sentinels
- coerce numeric-like text
- remove duplicate rows
- remove rows missing target
- identify/drop unusable constant columns
- identify extremely sparse columns
- handle missing feature values
- retain audit log

The system must record what happened.

The client does not see the low-level log.

The admin can inspect it.

---

# 11. Missing-Value Handling

There must be a deterministic rule layer.

Example default rules:

- no missing → keep
- numeric missing → median imputation
- categorical missing → most-frequent imputation
- mostly empty column → candidate for removal
- only a very small number of incomplete rows → may be candidate for row removal

Selective LLM assistance can be used for genuinely ambiguous cases.

Every decision should persist:

```text
column
evidence
rule decision
LLM output if called
validator verdict
final decision
fill value if any
source = rule | llm | fallback
```

Do not create a second parallel "hypothesis" logging system for the same decision.

The existing decision ledger should be generalized and reused.

---

# 12. Column-Type Handling

Automatically derive:

```text
numerical_cols
categorical_cols
datetime_cols
identifier_cols
ignored/free_text_cols
```

Do not hardcode column names.

Default modeling behavior:

- numeric dtypes → numeric unless evidence suggests category/identifier
- booleans → categorical
- low-cardinality strings → categorical
- identifiers → excluded
- high-cardinality free text → excluded from ordinary one-hot path
- datetimes → deterministic date/time transforms where implemented

LLM column-type assistance should be invoked only for ambiguous columns and must actually be wired into the running auto-train path.

---

# 13. Feature Engineering

Current phase: deterministic, controlled feature engineering.

Do not make the system Telco-specific.

No hardcoded:

```text
TotalChargesPerMonth
ServiceCount
ContractLength
```

unless those are implemented as optional domain packs outside the generic core.

Generic deterministic transforms may include:

- datetime decomposition/conversion
- safe numeric normalization via preprocessing
- other generic transformations that are valid across domains

Future experiment-based LLM feature engineering is paused.

The admin should distinguish:

- original features
- generated features
- removed features
- transformations

---

# 14. Leakage Safety

Leakage protection has higher priority than model score.

At minimum protect against:

- target included in features
- obvious duplicate target columns
- identifiers used improperly
- fitting preprocessing outside training/CV folds
- using test data for feature selection
- using test data for model selection
- using future/post-outcome fields when detectable
- train/test contamination

The final test set is not an optimization dataset.

---

# 15. Preprocessing Pipeline

Use sklearn pipelines so all fit-dependent preprocessing occurs only inside training folds.

Numeric default:

```python
Pipeline([
    ("imputer", SimpleImputer(strategy="median")),
    ("scaler", StandardScaler())
])
```

Categorical default:

```python
Pipeline([
    ("imputer", SimpleImputer(strategy="most_frequent")),
    ("onehot", OneHotEncoder(
        drop="first",
        handle_unknown="ignore",
        sparse_output=False
    ))
])
```

Combine with `ColumnTransformer`.

Adapt to installed sklearn versions when necessary.

Tree models do not inherently require scaling, but the first deterministic architecture may share a safe preprocessor where existing implementation expects it. Future model-specific preprocessing optimization can happen later.

---

# 16. Train/Test Integrity

Required high-level pattern:

```text
Full dataset
   ↓
Train/Test split
   ├── Train
   │    ↓
   │  Cross-validation
   │    ↓
   │  Candidate comparison
   │    ↓
   │  Final model locked
   │
   └── Test remains untouched
          ↓
       Final evaluation
          ↓
       Test predictions
```

Classification:

```python
train_test_split(..., test_size=0.2, random_state=42, stratify=y)

StratifiedKFold(
    n_splits=5,
    shuffle=True,
    random_state=42
)
```

Regression:

```python
train_test_split(..., test_size=0.2, random_state=42)

KFold(
    n_splits=5,
    shuffle=True,
    random_state=42
)
```

The test set must not fit:

- imputers
- scalers
- encoders
- feature selectors
- model parameters

The test set must not participate in CV or model selection.

---

# 17. Model Candidates

Initial generic model portfolio.

## Classification

- Logistic Regression
- Random Forest
- XGBoost when available
- LightGBM when available
- existing Gradient Boosting / Extra Trees if retained

## Regression

- Linear Regression
- Random Forest Regressor
- XGBoost Regressor when available
- LightGBM Regressor when available
- existing Gradient Boosting / Extra Trees if retained

No model should be chosen because the dataset is "Telco."

Models are selected based on task compatibility and validation evidence.

---

# 18. Evaluation

## Classification

Persist relevant metrics such as:

- ROC-AUC
- PR-AUC
- accuracy
- precision
- recall
- F1
- confusion matrix data where implemented

## Regression

Persist:

- MAE
- RMSE
- R²
- MSE if useful

Persist:

- per-fold scores
- CV mean
- CV standard deviation
- final test metrics
- candidate model comparison

Model selection should primarily use the configured validation metric.

The untouched test score is final evidence, not the model-selection input.

---

# 19. Predictions

After model selection is locked:

1. Fit the final pipeline on all training data.
2. Predict the untouched test set.
3. Persist predictions.
4. Persist probabilities/scores for classification where available.
5. Persist actual target (`y_true`) for evaluation output where appropriate.
6. Make downloadable results available.

The normal client sees a useful prediction table/result.

The admin can access the full technical prediction artifact.

---

# 20. Run State

Backend may maintain detailed stages:

```text
queued
ingesting
analyzing
cleaning
feature_engineering
preprocessing
splitting
cross_validation
training
evaluating
predicting
completed
failed
skipped
```

Client-facing state must be collapsed to:

```text
queued
processing
completed
failed
```

The client must NOT see a technical stage checklist.

Admin may see the internal summary.

---

# 21. Client UI

Canonical route:

```text
/lab/runs/{run_id}
```

## Processing

Show only:

```text
Analyzing your data

Your dataset is being processed.
This may take a while.
```

No technical checklist.

No fake percentage.

No fake timers.

## Completed

Show:

- dataset name
- record count
- feature count if meaningful
- target
- task
- primary test performance
- prediction count
- preview
- download action
- plain-language summary

## Failed

Show a clean failure state and useful user-safe message.

Do not leak stack traces.

## Refresh

Refresh must rehydrate from backend state.

---

# 22. Admin UI

Admin run detail should contain:

## Run Overview

- run
- dataset
- target
- task
- status
- start/completion
- duration

## Data Analysis

- rows
- columns
- missing
- duplicates
- constant columns
- high-cardinality columns
- numerical/categorical counts
- identifiers/suspicious fields where available

## Cleaning Log

Table:

```text
Column | Problem | Action | Result | Source/Why
```

Decision ledger details must allow the admin to determine what happened to each affected column and why.

## Feature Engineering

- original features
- generated features
- removed features
- transformations

## Validation

- train size
- test size
- CV type
- fold count
- random state

## Model Comparison

```text
Model | CV | Test | Selected
```

Use real persisted candidate results.

## Final Model

- model family
- selection metric
- CV result
- test result
- full relevant metrics

## Predictions

- prediction count
- distribution/summary where applicable
- downloadable prediction set

## Technical detail

Can be expandable:

- raw decision records
- pipeline log
- raw persisted metrics
- implementation details

---

# 23. Frontend/Backend Synchronization

This is mandatory.

The frontend must not maintain a second truth.

Required lifecycle:

```text
POST upload
  ↓
backend saves upload
  ↓
backend creates/links run
  ↓
response contains run_id
  ↓
frontend redirects
  ↓
GET run by run_id
  ↓
render persisted status
```

On completion:

```text
GET run
  ↓
completed
  ↓
persisted result
  ↓
render result
```

On refresh:

```text
direct URL
  ↓
GET backend state
  ↓
correct UI
```

Do not fake lifecycle transitions in React.

---

# 24. Current Repository Audit — Verified 2026-09-01

This section records what currently exists on `main` based on the repository inspection.

## 24.1 Implemented or substantially implemented

### Generic deterministic profiling

`apps/api/app/engine/schema/profiler.py`

Already calculates:

- rows
- columns
- dtypes
- missing values
- uniqueness/cardinality
- duplicates
- numerical statistics
- categorical distributions
- constants
- high cardinality
- identifier-like columns
- datetime-like columns
- suspicious columns

This is a useful generic foundation.

### Deterministic cleaning/preprocessing

`apps/api/app/engine/lab/auto_prepare.py`

Already implements:

- invalid missing sentinels
- infinite value cleanup
- numeric-like coercion
- duplicate removal
- target-missing row removal
- high-missing column handling
- constant-column removal
- missing-value default planning
- numeric/categorical role split
- sklearn `ColumnTransformer`
- median numeric imputation
- standard scaling
- most-frequent categorical imputation
- one-hot encoding with unknown handling
- datetime conversion

### Hidden auto-train pipeline

`apps/api/app/services/auto_train_service.py`

Already performs a substantial hidden workflow:

```text
ingest
→ profile
→ target heuristic
→ clean
→ missing decisions
→ feature engineering
→ column roles
→ preprocessing
→ experiment
→ CV/model training
→ results
→ persisted run status
```

### LLM decision foundation

Existing files include:

- `apps/api/app/engine/lab/llm_client.py`
- `apps/api/app/engine/lab/evidence.py`
- `apps/api/app/engine/lab/decision_validator.py`
- `apps/api/app/services/lab_decision_ledger.py`

The current LLM layer already supports schema-validated:

- missing-value decisions
- column-type decisions

with rule fallback and validator gating.

### Admin backend ML-run surface

Existing:

- `apps/api/app/api/admin_client_uploads.py`
- `apps/api/app/services/admin_client_uploads_service.py`
- `apps/api/app/services/admin_ml_run.py`

The admin response already assembles:

- run overview
- analysis
- processing summary
- cleaning
- feature engineering
- validation
- model comparison
- final model
- predictions

Prediction CSV download also exists.

### Admin UI detail

Existing:

`apps/web/app/admin/models/client-uploads/[id]/page.tsx`

Already shows much of:

- run overview
- data quality
- processing summary
- model comparison
- final model/evaluation
- predictions
- cleaning detail
- feature engineering
- missing-value audit
- raw log

### Client run/result route

Existing:

`apps/web/app/lab/runs/[run_id]/page.tsx`

Already has:

- processing state
- completed result
- predictions preview
- download
- refreshable backend-driven run hook

---

# 25. Current Repository Gaps and Conflicts

## P0 — General-purpose architecture is not complete

The current target/use-case mapping still depends on predefined business use cases.

Files:

- `apps/api/app/domain/lab_use_cases.py`
- `apps/api/app/engine/lab/column_map.py`
- `apps/api/app/engine/lab/auto_prepare.py`

The current code contains predefined use cases such as:

- churn
- conversion
- lead conversion
- purchase
- customer value

It contains hardcoded target aliases and business feature-group keyword lists.

This conflicts directly with the requirement:

> The core platform must understand arbitrary tabular datasets, not map every dataset into five predefined business cases.

These business definitions may remain only as optional examples/domain packs, not as the generic core inference system.

---

## P0 — Generic target understanding is incomplete

`pick_target_heuristic()` currently:

1. searches known target aliases from `LAB_USE_CASES`
2. otherwise looks for a clean binary yes/no-like column
3. otherwise fails

This is not sufficient for:

- arbitrary regression
- arbitrary binary labels with unfamiliar names
- multiclass
- semantic targets with unfamiliar names

Required replacement:

```text
explicit target if supplied
        ↓
deterministic target candidates
        ↓
LLM semantic ranking if ambiguous
        ↓
validator
        ↓
task inference
```

---

## P0 — Eight-case end-to-end finish-line test is missing

The requested file:

`apps/api/tests/test_e2e_lab_run.py`

does not exist on current `main`.

Therefore the deterministic pipeline cannot yet be considered finished under the agreed definition of done.

This test suite must be completed before future autonomous-agent work.

---

## P1 — Client processing UI exposes internal stages

Current:

`apps/web/app/lab/runs/[run_id]/page.tsx`

still includes a processing checklist with step markers.

This conflicts with the product requirement.

Client should see only:

```text
Analyzing your data
This may take a while.
```

Detailed pipeline stages are admin/internal only.

---

## P1 — Column-type LLM support exists but appears not wired into auto-train

`lab_decision_ledger.py` contains:

`record_column_type_decisions(...)`

but the inspected `auto_train_service.py` currently imports/calls `record_missing_value_decisions` and performs `split_column_roles(...)` directly.

The column-type decision integration must be verified and, if absent, wired into the real run path.

No parallel implementation should be created.

---

## P1 — Admin placement/navigation is inconsistent with desired Lab IA

The technical client-upload run page currently lives under:

```text
/admin/models/client-uploads/{id}
```

The desired product information architecture is centered on:

```text
/admin/lab/runs/{run_id}
```

The functionality largely exists, but navigation/location should be unified rather than duplicated.

Reuse components/services.

Do not build a second admin run detail implementation.

---

## P1 — Entity selection is not general enough

The auto-train `TaskSpec` currently uses the first retained feature as `entity_id` when necessary.

This is unsafe as a generic semantic assumption.

Identifier/entity selection must come from:

- explicit entity if provided
- deterministic identifier evidence
- semantic interpretation where appropriate
- otherwise `None` if the task supports no entity requirement

Do not turn the first arbitrary feature into an entity identifier.

---

## P1 — Feature engineering is intentionally minimal

Current generic feature engineering mostly converts datetime fields.

This is acceptable for the deterministic foundation, but it should be described accurately.

Do not claim advanced generic feature engineering is implemented when it is not.

Future experiment-based feature generation remains a later phase.

---

## P2 — Five-business-use-case catalog can remain only as optional layer

`LAB_USE_CASES`, `GROUP_KEYWORDS`, and target alias tables are useful for examples or optional accelerated business templates.

They must not control the generic upload path.

Desired architecture:

```text
Generic Core
     ↑
Optional Domain Packs
```

not:

```text
Five Domain Packs
     =
Generic Core
```

---

## P2 — CI status is not proven for the latest inspected commit

The inspected latest commit did not expose completed workflow/check status through the connected GitHub data.

Do not treat the repository as verified-green merely because code exists.

Run the required local/CI tests.

---

# 26. Relationship of the Two Older Tasks to the Current Plan

## Older Task A — Admin completed-run surface

This task does NOT conflict with the product idea.

It is aligned with the admin workflow.

Most of its backend and UI functionality now appears implemented.

It should be treated as:

```text
mostly implemented
→ verify
→ fix gaps
→ move under coherent Admin Lab navigation
```

Do not rebuild it from scratch.

Definition of done remains:

> Open a completed run in the admin UI and determine what was done to each affected column and why, which models were compared, which model won, and inspect/download predictions without touching the database directly.

Required verification:

```bash
pytest apps/api/tests/test_admin_surfaces.py -v
```

---

## Older Task B — Eight end-to-end verification cases

This task also does NOT conflict with the product idea.

It is essential.

It is the finish line for the deterministic pipeline.

It should happen before the future autonomous-agent architecture.

The suite is currently missing and must be implemented.

---

# 27. Eight Required End-to-End Tests

Create:

`apps/api/tests/test_e2e_lab_run.py`

Do not mock the core pipeline.

## Test 1 — Real classification lifecycle

A classification CSV must travel:

```text
upload
→ run created
→ real persisted processing
→ analysis
→ cleaning
→ feature engineering
→ split
→ 5-fold CV
→ multiple models
→ model selection
→ final test evaluation
→ predictions
→ persisted result
```

Also verify the client route uses the same real run.

---

## Test 2 — Missing values and preprocessing leakage

Use numeric and categorical missing values.

Verify:

- correct imputation
- imputer/scaler fitted only on training data
- test values do not influence fitted preprocessing statistics

Assert programmatically.

---

## Test 3 — Unseen categorical test value

Ensure:

```python
handle_unknown="ignore"
```

works when a category occurs only in the held-out test set.

---

## Test 4 — Regression

Use a generic regression dataset unrelated to Telco.

Verify:

- regression task
- `KFold`, not `StratifiedKFold`
- regression model portfolio
- MAE
- RMSE
- R²
- final predictions

---

## Test 5 — Invalid/unsupported dataset

Verify:

- run becomes `failed`
- real persisted error exists
- client does not spin forever
- no fake success/result

---

## Test 6 — Mid-processing refresh/direct URL

Verify backend state rehydrates the client.

No local-only lifecycle.

---

## Test 7 — Completed refresh/direct URL

Verify persisted completed result loads.

No retrain.

No mocked cache.

---

## Test 8 — Programmatic test-set integrity

Verify:

- test set never fits preprocessing
- test set never participates in CV
- model selection is locked before final test metric calculation

This is a hard invariant.

---

# 28. Additional General-Purpose Verification Tests

After the eight core tests, add generic-schema tests.

At minimum:

## Dataset A — unfamiliar classification

```text
age
income
region
defaulted
```

## Dataset B — unfamiliar regression

```text
temperature
humidity
pressure
energy_output
```

## Dataset C — unfamiliar fraud-style classification

```text
transaction_amount
merchant_type
country
fraud_flag
```

## Dataset D — arbitrary target name

```text
f1
f2
f3
outcome_x
```

The generic target-understanding layer should not depend on known business aliases.

## Dataset E — integer categorical code

```text
region_code = 1, 2, 3, 4
```

Test deterministic/LLM column-type interpretation.

---

# 29. Codex Implementation Order

Execute one step at a time.

Do not hand Codex the whole roadmap as one implementation task.

---

## STEP 1 — Freeze the product contract and audit current main

Goal:

- compare repository to this document
- mark implemented / partial / missing
- make no architecture duplication

Deliverable:

`docs/DCLAB_IMPLEMENTATION_STATUS.md`

No new feature implementation in this step.

Verify relevant files and current tests.

---

## STEP 2 — Remove hardcoded use-case control from generic upload path

Goal:

The automatic upload path must no longer require `LAB_USE_CASES` target aliases or business feature keywords to function.

Actions:

- separate optional business templates from generic core
- refactor `pick_target_heuristic`
- refactor generic column/entity/target logic
- preserve existing specialized Lab tasks where needed, but do not use them as the core arbitrary-upload inference mechanism

Tests:

Use multiple unrelated schemas.

---

## STEP 3 — Generalize target/task candidate engine

Implement deterministic:

```text
TargetCandidate
TaskCandidate
```

with evidence and confidence.

Support:

- explicit target override
- arbitrary binary target
- generic regression candidate
- identifier rejection
- no "last column" fallback

Persist target selection evidence.

---

## STEP 4 — Add small-LLM dataset semantic interpretation

Reuse the existing:

- LLM client
- evidence architecture
- validator pattern
- decision ledger philosophy

Do not build a parallel agent framework.

Add structured decisions for:

- target ranking
- task interpretation if ambiguous
- identifier/entity interpretation if needed
- other genuine semantic ambiguity

Use configurable model name.

Fail closed.

Use deterministic fallback.

---

## STEP 5 — Wire existing column-type LLM path into real auto-train

Verify whether `record_column_type_decisions()` is currently unused.

If unused:

```text
split_column_roles
        ↓
identify ambiguous roles
        ↓
record_column_type_decisions
        ↓
validator
        ↓
final numerical/categorical/identifier roles
```

Persist decision records.

Do not create a second column-type decision service.

---

## STEP 6 — Fix generic entity handling

Do not assign the first feature as an entity ID.

Implement:

- explicit entity
- identifier candidate
- semantic validated entity
- or no entity

Adapt validation strategy accordingly.

---

## STEP 7 — Preserve and verify deterministic ML pipeline

Do not redesign what already works.

Verify:

- cleaning
- preprocessing
- `ColumnTransformer`
- train/test
- 5-fold CV
- classification/regression
- candidate models
- final model selection
- final test prediction
- persistence

Fix only real gaps.

---

## STEP 8 — Fix client run processing UX

Remove technical processing checklist from normal client page.

Client:

```text
UPLOAD
→ PROCESSING
→ RESULT
```

Detailed stages remain backend/admin only.

Ensure refresh/deep links use backend state.

---

## STEP 9 — Consolidate admin run UI under Admin Lab

Reuse current admin client-upload ML run page and its APIs.

Do not rewrite.

Move/re-route/refactor into coherent Admin Lab information architecture.

Target canonical route:

```text
/admin/lab/runs/{run_id}
```

Old route can redirect for compatibility.

---

## STEP 10 — Verify the older admin-surface task

Run:

```bash
pytest apps/api/tests/test_admin_surfaces.py -v
```

Fix any failures.

Definition of done:

Admin can answer:

- what happened to affected columns
- why
- which models were compared
- which model won
- final metrics
- prediction count
- prediction download

without direct DB access.

---

## STEP 11 — Implement the eight-case E2E finish-line suite

Create:

```text
apps/api/tests/test_e2e_lab_run.py
```

Implement all eight agreed cases.

No mocked core ML services.

This is mandatory.

---

## STEP 12 — Add general-purpose dataset tests

Prove the system is not a Telco implementation.

Run unrelated classification/regression schemas.

Assert no code path requires Telco/business-specific fields.

---

## STEP 13 — Frontend/backend synchronization verification

Verify:

- upload returns real run
- redirect uses real ID
- processing reads backend
- result reads persisted backend
- predictions belong to same run
- refresh works
- direct URL works
- failure works
- admin and client refer to same persisted run

No frontend fake state.

---

## STEP 14 — Final UI cleanup

Only after behavior is correct.

Client:

```text
Upload
Processing
Result
Predictions
```

Admin:

```text
Overview
Datasets
Runs
Experiments
Models
Predictions
```

Use a single coherent design system.

No fake cards or disconnected dashboards.

---

# 30. Critical Codex Rules

1. Search before creating files/classes/services.
2. Reuse existing ML engine.
3. Reuse existing decision ledger.
4. Reuse existing admin run assembler.
5. Do not create a new "run" system.
6. Do not create a new experiment system.
7. Do not build autonomous agents yet.
8. No Telco-specific production logic.
9. No hardcoded target names.
10. No fake frontend progress.
11. No mock production result data.
12. Backend is the source of truth.
13. LLM receives compact evidence, not the full dataset by default.
14. LLM output must be schema-validated.
15. LLM failure must not crash the deterministic pipeline.
16. Test set integrity is mandatory.
17. Finish one vertical slice before adding breadth.
18. Do not claim completion because files/classes exist.
19. Completion means the actual runtime behavior passes tests.
20. Preserve working specialized features as optional layers without allowing them to define the generic core.

---

# 31. Final Product Mental Model

The simplest correct description of DCLab in this phase is:

```text
                DCLab
                  │
          ANY TABULAR DATA
                  │
                  ▼
        DETERMINISTIC PROFILER
                  │
          structured evidence
                  │
                  ▼
       SEMANTIC UNDERSTANDING
     rules + small LLM if needed
                  │
                  ▼
          GENERIC ML PIPELINE
                  │
     ┌────────────┼────────────┐
     ▼            ▼            ▼
 cleaning    preprocessing   features
     │            │            │
     └────────────┼────────────┘
                  ▼
           TRAIN / VALIDATE
                  │
                  ▼
          MODEL COMPARISON
                  │
                  ▼
            FINAL MODEL
                  │
                  ▼
        UNTOUCHED TEST SET
                  │
                  ▼
             PREDICTIONS
                  │
        ┌─────────┴─────────┐
        ▼                   ▼
      CLIENT              ADMIN
        │                   │
 simple outcome       technical evidence
 predictions          decisions
                      model comparison
                      validation
                      logs
```

The current platform is the foundation for a broader Decision Intelligence system later.

The immediate objective is not the future autonomous agent.

The immediate objective is to make this generic deterministic + selective semantic-assistance workflow correct, traceable, reusable, and fully synchronized from backend to frontend.

---

# 32. Future Phase After This Is Green

Only after the deterministic/general-purpose foundation passes all tests should DCLab add:

```text
Feature hypotheses
        ↓
Experiment-based feature engineering
        ↓
Experiment graph
        ↓
ML Critic
        ↓
Iterative experiment loop
        ↓
Autonomous Data Scientist
        ↓
Business decision/action layer
```

Those are later intelligence layers built on the reliable core defined above.
