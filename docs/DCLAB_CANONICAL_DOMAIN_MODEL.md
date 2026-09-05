# DCLab canonical domain model

This is the frozen Prompt 9 customer/ML core. Personal and Business tenants share
this model and **one** training engine (`run_auto_train_job` → `run_experiment`).
Internal DCLab roles stay off the customer membership table.

Do **not** treat this document as a license to add agent architecture, universal
deployment, NLP/vision, or enterprise integrations.

## Tenant and case

```
Workspace
  → Project
      → ProblemSpec
```

- **Workspace** is the tenant boundary (`personal` or `business`).
- **Project** is a first-class case study. It is not a Workflow and not an Experiment.
- **ProblemSpec** is a versioned intent statement (task type, target, metric, constraints).
  It is upstream of Lab `PredictionTask` / TaskSpec.

Customer technical APIs are workspace-scoped. `dclab_admin` may inspect every
tenant. `dclab_developer` may inspect every tenant and must not perform
side-effecting writes.

## Dataset lineage

```
DataSource
  → IngestionRun
      → DatasetAsset
          → Dataset          (physical version; immutable)
              → DatasetColumn
```

Bytes live in provider-neutral object storage. PostgreSQL stores `Artifact`
registry rows (digest, key, size), not blobs.

`client_lab_uploads` is the Labs **compatibility adapter** into this lineage.
It is not a second ingest engine. Auto-train then writes a second immutable
`datasets` row for the prepared CSV and **reuses** the upload `IngestionRun`
so PipelineRun / ModelVersion still resolve DataSource without a second trainer.

## Execution definitions and runs

```
Workflow
  → WorkflowVersion
      → WorkflowRun

Pipeline
  → PipelineVersion
      → PipelineRun          (physical table: experiments)
          → PipelineStageRun
```

`experiments` remains the physical PipelineRun table. Do not add a parallel run
table or a second trainer.

## Scientific modeling

```
Dataset
  → DataPreparationDecision / DataQualityFinding
  → FeatureSet → FeatureSetVersion → Feature → FeatureLineage / FeatureTransformation
  → PreprocessingStep
  → Candidate                (physical table: experiment_candidates)
      → ModelHyperparameter
      → CVFoldRun
      → ModelEvaluation → EvaluationMetric
  → ModelSelectionDecision   (explicit winner lock; CV-only)
  → ModelAsset → ModelVersion
      → Artifact
      → CodeSnapshot
      → RuntimeEnvironment
```

Winner selection is a row, not an inferred JSON field. Final holdout is a
`ModelEvaluation` with `evaluation_scope = final_holdout` after the lock.

## Roles

| Plane | Roles | Notes |
| --- | --- | --- |
| Customer workspace | `workspace_owner`, `workspace_admin`, `ml_engineer`, `viewer` | Stored on `workspace_memberships`. Legacy `business_admin` / `business_developer` / `client_user` still resolve. |
| Platform | `dclab_admin`, `dclab_developer` | Stored on `platform_memberships`. Never a customer seat. |

`max_members` counts **every** workspace membership, including owner and admins.
A business default of 5 is five total seats, not five ML-engineer seats plus admins.

## One ML core

Labs CSV upload, admin Lab train, and auto-train all persist through the same
execution and scientific writers. JSONB on `experiments.result` and
`experiment_candidates.payload` remains compatibility evidence beside the
canonical tables.

The Labs adapter creates one `client-lab-analysis` workflow per Project. The
canonical slug is used for the first Project in a workspace; later Projects get
`client-lab-analysis-{project.slug}` so a ProblemSpec cannot attach to another
case study's workflow.

Missing-value `keep` decisions are persisted. A clean CSV still has queryable
data-preparation rows; imputation/drop rows are not the only recorded decisions.

## Release gate (Prompt 9)

This redesign is complete only while all of the following remain true:

- Personal and Business tenants share one ML core
- Internal DCLab roles are separate from customer workspace roles
- Project and ProblemSpec are first-class
- Object storage is provider-neutral (`local` / `s3` / `gcs`)
- Dataset, workflow/pipeline versions, pipeline execution, data-preparation
  decisions, features, preprocessing, candidates, hyperparameters, CV folds,
  metrics, and explicit winner selection are queryable
- Code / runtime / artifact reproducibility exists
- ModelVersion has complete lineage
- Customer technical access is workspace-scoped
- `dclab_admin` can inspect all technical evidence
- `dclab_developer` remains platform read-only
- Critical tenant corruption is DB-blocked
- Legacy working paths remain operational
- Fresh and existing-database Alembic upgrades succeed
- Full backend tests pass

Out of scope for this freeze: agent architecture, universal deployment,
NLP/vision, and enterprise integrations.
