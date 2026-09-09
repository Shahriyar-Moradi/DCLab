# DCLab Pipeline Observability

## Scope and identity

Pipeline observability follows the production lineage:

`Workspace → MlWorkflow → WorkflowRun → Experiment/PipelineRun`

An `Experiment` is the technical Pipeline Run. Events and LLM invocations always
carry `workspace_id`, `workflow_run_id`, and `experiment_id`; services validate
those identities before persisting or returning records. Observatory writes do not
alter experiment results, candidate selection, predictions, or managed model
versions.

## Replayable event stream

`ml_run_events` is an append-only, bounded event stream. `sequence` is unique and
monotonically allocated inside a Pipeline Run. PostgreSQL rejects updates and
deletes through the `ml_run_events_append_only` trigger, and the ORM applies the
same rule in application/test paths.

Events are emitted only at real execution boundaries. The current Labs pipeline
covers ingestion, profiling/EDA, target and task resolution, structural cleaning,
holdout planning and locking, Adaptive Model Builder planning (problem profile, validation
plan, metric plan, leakage audit, locked model-development plan — once per production run), train-only
decisions, missing-value decisions, column roles, feature engineering,
preprocessing configuration, candidate and CV-fold lifecycles, selection, winner
lock, final fit, winner-only final test, predictions, artifact persistence,
deterministic verification, OpenAI audit, report generation, and terminal state. A failed candidate produces a failure
event without hiding successful candidates; a failed run retains its prior event
history and terminal failure.

Payloads are recursively sanitized and size bounded. Secret-bearing keys and
values, raw/sample row collections, and row-provenance arrays are redacted. The
event writer locks the Pipeline Run while allocating a sequence, so concurrent
writers cannot produce duplicate sequence numbers. Event persistence failures are
isolated from ML execution.

## LLM invocation ledger

`llm_invocations` is the generic, safe LLM observability ledger. Supported
purposes are deliberately separated:

- Semantic decisions: `semantic_target`, `semantic_missing_value`,
  `semantic_column_type`, and `semantic_leakage`.
- Advisory pipeline audits: `pipeline_audit_routine` and
  `pipeline_audit_deep`.

Every monitored semantic decision says whether an LLM was used, why, its purpose,
prompt/schema version, validator verdict, and the final accepted decision. When a
rule is sufficient, the canonical reason is:

`LLM used: NO — deterministic evidence was sufficient.`

Provider and model are present only when a provider was actually attempted. The
ledger persists an evidence digest rather than evidence. Its safe output excludes
raw rows, sample values, provider rationale, and domain fill values. Token usage is
stored when returned by the provider. Estimated cost remains null unless a stable,
safe pricing source is available; it is never guessed.

`LabDecisionRecord` remains authoritative for semantic decisions and references
the generic ledger through `llm_invocation_id`. `MlRunVerification` remains
authoritative for deterministic/advisory verification attempts and uses the same
reference pattern.

Routine/deep OpenAI audit records expose the audit mode, Luna/Terra model,
evidence digest, production redaction summary, deterministic status, advisory
status, warnings, critical issues, confidence, and recommendations. They cannot
be confused with semantic decisions because their purposes are constrained to a
different purpose set at both service and database layers.

## Read APIs and tenant enforcement

Platform readers use `/admin/observatory`; workspace readers use the technical
business-administration surface `/business/observatory`. That tree requires
`require_business_administration` (platform or Business Admin/Developer) plus
`require_workspace_read`. Legacy `client_user` tokens receive `403`. The
translated end-user `/app` surface remains separate.

Both surfaces provide:

- `GET /pipeline-runs/{id}/summary`
- `GET /pipeline-runs/{id}/events`
- `GET /pipeline-runs/{id}/events/incremental?after_sequence=N`
- `GET /pipeline-runs/{id}/llm-invocations`
- `GET /llm-invocations/{id}`
- `GET /workflow-runs/{id}/pipelines`

Platform members may read across authorized businesses. Business requests always
apply the validated workspace context inside the database query. A workspace
header or route ID never grants access; an object belonging to another workspace
returns `404` after workspace authorization.

## Behavioral invariants

- Holdout lock precedes every CV event.
- Holdout plan selection precedes holdout lock.
- Problem profile, validation plan, metric plan, leakage audit, and
  model-development plan events precede CV and occur once per production run.
- Winner lock precedes final-test evaluation.
- The final test is evaluated once and only for the locked winner.
- Fold start/completion and candidate failure remain visible.
- Semantic LLM decisions and OpenAI pipeline audits have disjoint purposes.
- Observability callbacks do not change deterministic ML outputs.
- Complete datasets, raw rows, row provenance, API keys, and bearer tokens are
  prohibited from event and LLM payloads.

Planning event types: `holdout_plan_selected`, `holdout_locked`,
`problem_profile_started`, `problem_profile_completed`,
`validation_plan_selected`, `metric_plan_selected`, `leakage_audit_started`,
`feature_leakage_warning`, `feature_excluded_for_leakage`,
`leakage_audit_completed`, `model_development_plan_locked`.

See [DCLAB_ADAPTIVE_MODEL_BUILDER.md](DCLAB_ADAPTIVE_MODEL_BUILDER.md).
