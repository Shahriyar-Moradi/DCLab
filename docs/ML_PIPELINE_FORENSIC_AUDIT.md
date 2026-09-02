# DCLab ML Pipeline Forensic Audit

**Scope:** deterministic automatic tabular pipeline (`open_ingest`)  
**Basis date:** 2026-09-01  
**Phase:** ML-2 scientific correctness and instrumentation

## Executive finding

The existing pipeline already used sklearn `Pipeline` + `ColumnTransformer`
inside cross-validation folds, but two orchestration defects prevented a clean
scientific audit:

1. missing-value, identifier, column-role, and datetime decisions were derived
   from the complete dataset before the final holdout was locked;
2. candidate ranking used CV, but every trained candidate was subsequently fit
   and scored on the final test set.

These were correctness defects, not reasons to replace the experiment or run
systems. The required ML-2 correction is a narrow refactor of the existing
automatic upload path.

## Audited runtime before ML-2

```text
load
→ profile / target
→ full-frame cleaning and missing decisions
→ full-frame column roles / feature decisions
→ persist prepared table
→ split
→ fold-isolated CV
→ select winner by CV
→ fit and test every trained candidate
→ persist winner predictions
```

## Scientifically necessary corrections

### A. Split structural hygiene from learned decisions

Allowed before the split:

- file loading;
- column normalization;
- recognized missing-sentinel normalization;
- numeric-like parsing;
- duplicate removal;
- target resolution and unusable-target row removal.

Required after the split, using training rows only:

- sparse/constant/identifier eligibility evidence;
- missing-value policy and missingness/target evidence;
- LLM missing-value evidence;
- column-role ambiguity and LLM role evidence;
- datetime/feature transformation eligibility;
- leakage screening that can remove modeled columns.

### B. Lock and persist the holdout early

The automatic path must use the existing deterministic 80/20 split:

- binary: stratified, random state 42;
- regression: unstratified, random state 42.

Persist source-row provenance for train and test, plus an explicit disjointness
flag. Repeating the split after persisting the prepared table must reproduce the
same test source rows exactly.

### C. Preserve fold isolation

Each fold must construct a fresh sklearn pipeline containing:

- numeric median imputer and standard scaler;
- categorical most-frequent imputer and one-hot encoder;
- the candidate estimator.

No imputer, scaler, encoder category, or estimator may be fit on holdout rows.

### D. Lock the winner before the holdout

Candidate comparison is CV-only. Persist a selection checkpoint containing:

```json
{
  "selected_candidate_id": "...",
  "selection_metric": "...",
  "selection_source": "cv",
  "locked": true,
  "locked_at": "..."
}
```

Commit that checkpoint to `Experiment.result` before final fit/test begins.
Only the selected candidate may receive `test_metrics`; rejected candidates
must persist `test_metrics = null`.

### E. Make feature evidence truthful

Persist separate lists for:

- `original_features`;
- `generated_features`;
- `transformed_features`;
- `removed_features`;
- `feature_engineering_actions`.

The current generic engine creates no new derived columns, so
`generated_features = []` is the truthful value. Existing datetime-to-Unix
conversion remains a deterministic transformed-feature action selected from
training evidence and applied unchanged to both partitions.

### F. Instrument real execution

Measure wall-clock stage execution without sleeps or synthetic delays. Each
stage record includes timestamps, elapsed milliseconds, row counts, and status.
Every candidate also records fold metrics, CV mean/std, preprocessing config,
fit duration, status, and failure reason.

### G. Persist one canonical technical report

Extend the existing `Experiment.result`; do not create another run system. The
canonical report must include run/dataset identity, raw profile, target/task,
split provenance, cleaning, roles, features, preprocessing, candidates,
selection, winner-only final evaluation, prediction summary, stage timings, and
decision ledger records.

The admin DOCX must be generated only from this persisted object.
The implementation uses the maintained `python-docx` dependency to render that
persisted object as `DCLab ML Run Report.docx`.

## Corrections explicitly out of scope

- frontend/admin information-architecture reconstruction;
- autonomous agents, critics, or iterative experiment loops;
- domain-specific Telco features;
- uncontrolled interactions or polynomial features;
- a replacement experiment, run, or decision-ledger system;
- using the final test set to tune thresholds, candidates, or preprocessing.

## Verification requirements

Tests must prove:

1. holdout extremes cannot change imputer or scaler state;
2. a holdout-only category is absent from encoder categories;
3. LLM missing-value evidence contains training targets only;
4. CV row counts equal the training partition, never the full dataset;
5. all candidates receive five-fold CV evidence;
6. winner selection is checkpointed before the holdout evaluator runs;
7. the holdout evaluator is called exactly once;
8. rejected candidates persist no final-test metrics;
9. provenance partitions are disjoint;
10. stage and candidate timings are measured and positive where meaningful;
11. generated/transformed feature lists are truthful;
12. classification and regression still complete and persist;
13. the DOCX is built from the canonical persisted report;
14. rejected-candidate holdout metrics cannot appear in the DOCX;
15. refresh/read endpoints continue to use the same persisted run.

## Verification Phase 2 timing semantics

The pipeline records five non-overlapping meanings and does not ask the
deterministic verifier to validate its own enclosing duration:

- `ml_execution_total`: background-job start through persistence of the ML
  artifacts and result evidence. It excludes deterministic verification,
  report generation, and OpenAI work.
- `deterministic_verification`: one read-only `PipelineVerifier` pass over the
  persisted ML evidence. Its invariant checks require only the core ML stages.
- `report_generation`: assembly of the canonical technical report after the
  deterministic result exists.
- `llm_verification`: one requested advisory OpenAI verification attempt,
  including at most one bounded retry at the provider boundary.
- `workflow_elapsed`: first workflow start through the latest terminal step
  represented by the report. When an OpenAI attempt exists, it ends at that
  attempt's terminal completion time; otherwise it ends after report generation.

OpenAI verification is a separate advisory layer. Each request persists its
own deterministic snapshot, redaction counts, SHA-256 input digest, provider
metadata, strict structured result or safe error class, and real timing. A
reverification overlays the latest attempt when the canonical report is read;
it does not mutate or retrain the original ML result.
