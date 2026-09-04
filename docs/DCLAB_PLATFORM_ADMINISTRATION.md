# DCLab Platform Administration

## Navigation hierarchy

The platform administration UI follows persisted lineage rather than presenting
independent registries:

`Business → Domain → Workflow → Workflow Run → Pipeline Run → Model → Pipeline Monitor`

`Workspace` is the Business tenant. Domain navigation is loaded from
`BusinessDomain` and `WorkspaceDomain`; adding another configured domain requires
no UI route or schema change. A Workflow Run page renders every related
`Experiment` independently, including failed pipelines and runs with more than one
pipeline.

The primary entry point is `/admin/businesses`. Business profiles expose overview,
domains, workflows, managed models, workflow runs, and workspace memberships.

## Permissions

The same pages serve both platform roles:

- `dclab_admin` can read and use side-effecting controls.
- `dclab_developer` has identical platform visibility, while write controls are
  disabled with a read-only explanation. Backend method guards remain the
  authoritative enforcement and return `403` for unsafe methods.

Existing Labs pages follow the same permission-aware behavior. A platform admin
uploading through Labs lands on the administrative run record and can navigate
directly to the Workflow Run and Pipeline Monitor. Ordinary business/client Labs
navigation remains separate.

## Pipeline Monitor

`/admin/pipeline-runs/{experiment_id}/monitor` is backed entirely by persisted
lineage, append-only events, candidate records, verification records, and safe LLM
invocation records. Completed two-second runs are replayed as recorded; the UI does
not delay ML execution.

The monitor displays:

- ingestion, EDA, target/task, cleaning, holdout, missing-value, column-role,
  feature-engineering, preprocessing, final-fit, final-test, prediction, artifact,
  report, and terminal stages;
- every candidate and CV fold, with CV-only comparison and `NOT EVALUATED` for a
  rejected candidate's final test;
- numerical `Median Imputer → StandardScaler` and categorical
  `Most-Frequent Imputer → OneHotEncoder` with `drop=first` and
  `handle_unknown=ignore`;
- train-only and fold-only fitting guarantees;
- every deterministic check under the explicit statement
  `DETERMINISTIC VERIFICATION = AUTHORITATIVE`;
- semantic LLM participation separately from the OpenAI pipeline auditor, which is
  labeled `OPENAI AUDIT = ADVISORY`;
- append-only timeline/replay, safe reports, and bounded sanitized evidence.

Prediction rows, full datasets, API keys, secrets, provider rationale, local input
paths, fill values, and row provenance are excluded from the monitor response.
