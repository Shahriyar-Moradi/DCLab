# DCLab database ERD

Logical model at Alembic head `0039_scientific_plans`. Physical table names
that differ from the logical noun are noted in parentheses.

```mermaid
erDiagram
    Workspace ||--o{ Project : contains
    Workspace ||--o{ WorkspaceMembership : authorizes
    Workspace ||--o{ WorkspaceEntitlement : limits
    Project ||--o{ ProblemSpec : states
    Project ||--o{ DataSource : owns
    DataSource ||--o{ IngestionRun : runs
    Project ||--o{ DatasetAsset : owns
    DatasetAsset ||--o{ Dataset : versions
    Dataset ||--o{ DatasetColumn : describes
    Dataset }o--o| Artifact : bytes
    Project ||--o{ Workflow : defines
    Workflow ||--o{ WorkflowVersion : versions
    Workflow ||--o{ WorkflowRun : executes
    WorkflowVersion ||--o{ WorkflowRun : pins
    ProblemSpec ||--o{ WorkflowRun : intends
    Workflow ||--o{ Pipeline : contains
    Pipeline ||--o{ PipelineVersion : versions
    PipelineVersion ||--o{ PipelineRun : executes
    WorkflowRun ||--o{ PipelineRun : contains
    PipelineRun ||--o{ PipelineStageRun : stages
    PipelineRun ||--o| PipelineScientificPlan : plans
    PipelineRun ||--o{ DataPreparationDecision : records
    PipelineRun ||--o{ FeatureSet : produces
    FeatureSet ||--o{ FeatureSetVersion : versions
    FeatureSetVersion ||--o{ Feature : contains
    PipelineRun ||--o{ PreprocessingStep : fits
    PipelineRun ||--o{ Candidate : searches
    Candidate ||--o{ ModelHyperparameter : applies
    Candidate ||--o{ CVFoldRun : folds
    Candidate ||--o{ ModelEvaluation : scores
    ModelEvaluation ||--o{ EvaluationMetric : names
    PipelineRun ||--o| ModelSelectionDecision : locks
    Workflow ||--o{ ModelAsset : registers
    ModelAsset ||--o{ ModelVersion : releases
    PipelineRun ||--o{ ModelVersion : publishes
    Candidate ||--o{ ModelVersion : selected
    ModelVersion }o--o| Artifact : model
    ModelVersion }o--o| CodeSnapshot : source
    ModelVersion }o--o| RuntimeEnvironment : runtime
    CodeSnapshot }o--o| RuntimeEnvironment : fingerprint
    CodeSnapshot }o--o| Artifact : lockfile
```

PipelineRun is the `experiments` table. Candidate is `experiment_candidates`.
`PipelineScientificPlan` is `pipeline_scientific_plans` (one row per run).
`RuntimeEnvironment` is a globally reusable fingerprint. The dependency-lock
Artifact is workspace-owned and referenced from `CodeSnapshot`.

## Compatibility and legacy (not first-class Project children)

| Table | Classification | Project FK |
| --- | --- | --- |
| `experiments` | Canonical compatibility table (PipelineRun) | nullable, backfilled |
| `experiment_candidates` | Canonical compatibility table (Candidate) | nullable, backfilled |
| `client_lab_uploads` | Labs adapter into DataSource/Ingestion/Dataset | via dataset/experiment |
| `opportunities` / `predictions` / `decisions` | Legacy product-scoring path, still written | workspace only |
| `client_lab_runs` / `client_lab_run_audits` | Legacy catalog trials | workspace only |
| `environments` / `prediction_tasks` | Legacy Lab execution containers | none |

See `docs/DCLAB_DATABASE_MIGRATION_MAP.md` for backfill rules.

## Object storage

`artifacts` is registry metadata only (`provider` in `local`, `s3`, `gcs`).
The blob is addressed by `object_key` + `content_digest`.
