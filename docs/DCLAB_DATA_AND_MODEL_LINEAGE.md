# DCLab Data and Model Lineage

## Canonical hierarchy

```text
Business (Workspace)
└── Domain (BusinessDomain enabled by WorkspaceDomain)
    └── Workflow (MlWorkflow)
        └── Workflow Run (WorkflowRun)
            ├── Inputs (WorkflowRunInput → Dataset)
            └── Pipeline Run (Experiment)
                ├── Candidate Models (ExperimentCandidate)
                ├── Holdout Evidence (ExperimentTestPrediction)
                └── Selected Model Version (ModelVersion → ModelAsset)
```

This hierarchy extends the existing deterministic ML platform. It does not add
a second experiment engine, change model selection, or alter the train/validation/
holdout algorithms.

## Terminology is structural

The following names are not interchangeable:

- **Workflow ≠ WorkflowRun.** `MlWorkflow` is reusable configuration and a
  business objective. `WorkflowRun` is one invocation, so rerunning the same
  workflow creates another row.
- **WorkflowRun ≠ PipelineRun.** A workflow invocation coordinates inputs and
  may contain several technical pipeline runs. The existing `Experiment` table
  is the PipelineRun implementation.
- **PipelineRun ≠ CandidateModel.** One Experiment evaluates multiple existing
  `ExperimentCandidate` rows.
- **CandidateModel ≠ ModelVersion.** Candidates are alternatives evaluated in a
  pipeline. A `ModelVersion` is the one immutable selected winner published into
  a logical `ModelAsset`.

These distinctions are enforced by separate tables and foreign keys. No alias
table duplicates Dataset, Experiment, ExperimentCandidate, or holdout prediction
evidence.

## Business and domain configuration

`Workspace` remains the canonical Business tenant. `business_domains` is a
global configurable catalog, and `workspace_domains` enables/configures catalog
entries for individual workspaces.

The initial catalog contains:

- `labs`
- `marketing`
- `sales`
- `revenue`
- `customer`

Domain values are rows, not database schemas, enums, or domain-specific tables.
`seed_business_domains` is idempotent, and a future domain can be inserted and
enabled through the same data service without an Alembic migration.

## Dataset lineage

`DatasetAsset` is the logical, workspace-owned dataset. The existing `Dataset`
is the physical/versioned dataset record and now contains:

- `workspace_id`
- `dataset_asset_id`
- `version`
- `content_digest`
- the existing physical location, schema, row count, and column count

`(dataset_asset_id, version)` and `(dataset_asset_id, content_digest)` are unique.
New local datasets receive a SHA-256 content digest. Legacy datasets are safely
backfilled to a logical asset; their digest remains nullable because a database
migration cannot truthfully hash an external or unavailable source file.

Physical Dataset rows are append-only in the ORM. Changing content requires a
new version. Existing `ClientLabUpload.dataset_id` remains the source-upload link,
with a reverse Dataset relationship, so no competing upload or execution dataset
table was introduced.

`WorkflowRunInput` provides a many-input boundary. Each input has a string role
such as `training`, `scoring`, `reference`, or `validation`. Roles are data rather
than a database enum, allowing future roles without schema changes. A Dataset may
be reused by any number of WorkflowRuns.

## Workflows and invocations

`MlWorkflow` stores the workspace, enabled workspace domain, name, slug,
description, business objective, status, JSON configuration, creator, and
timestamps. Workflow slugs are unique per workspace.

`WorkflowRun` records one invocation with its workspace, workflow, requester,
trigger type, source type, optional source upload, explicit and resolved targets,
task type, status, and execution timestamps. Services always create a new run;
they never implicitly deduplicate by Dataset or upload content.

The Labs upload path creates or reuses the workspace's `client-lab-analysis`
Workflow, then creates a distinct WorkflowRun for every accepted upload. The raw
uploaded Dataset is recorded as a reference input. It also creates one
`Experiment` PipelineRun shell immediately, before asynchronous execution. The
shell keeps the existing `ClientLabUpload.experiment_id` stable and makes early
ingestion or target-resolution failures traceable. When automatic preparation
creates the actual training Dataset and resolves the prediction task, it binds
those inputs to the same PipelineRun rather than creating a second run.

Accepted non-tabular uploads also receive a DatasetAsset and content-addressed
Dataset record. Their preview metadata may be incomplete and they can be marked
skipped by the existing eligibility rules, but their upload lineage is not lost.

## Pipeline runs and candidates

`Experiment` remains the technical PipelineRun and retains all existing engine
fields and behavior. It now adds:

- `workspace_id`
- optional `workflow_run_id` for backward compatibility
- `pipeline_name`
- `pipeline_index`
- `pipeline_purpose`

`(workflow_run_id, pipeline_index)` is unique. A WorkflowRun can therefore own
multiple ordered Experiments, while older internal Lab Experiments may remain
unattached until migrated. `ExperimentCandidate` and
`ExperimentTestPrediction` remain unchanged as the candidate-model and holdout
evidence layers.

The generic Labs path creates exactly one PipelineRun today. The service accepts
ordered pipeline indexes for future multi-pipeline workflows; it does not create
placeholder challenger pipelines.

The lineage service requires a PipelineRun's Dataset and WorkflowRun to belong
to the same Workspace and records the Dataset as a workflow input before the
Experiment is created.

## Managed models

`ModelAsset` is a logical managed model belonging to one Workspace and Workflow.
It may have multiple `ModelVersion` releases.

`ModelVersion` is append-only and contains direct foreign-key traces to:

- its `ModelAsset`;
- the selected `ExperimentCandidate`;
- the `Experiment`/PipelineRun;
- the `WorkflowRun` invocation;
- the reusable `MlWorkflow`;
- the owning `Workspace`;
- the physical/versioned `Dataset` used by the pipeline.

There is at most one ModelVersion per PipelineRun and per selected candidate.
Publishing validates that the candidate belongs to the pipeline, matches the
winner locked in the existing deterministic result, and that the pipeline Dataset
is one of the WorkflowRun inputs. A SHA-256 digest covers the candidate identity,
candidate payload, pipeline, and artifact URI. Publishing another release creates
a new row; existing ModelVersion rows cannot be updated or deleted through the
ORM.

## Tenant enforcement

All lineage creation services compare the Workspace on both ends of every
business-scoped edge before persistence. They reject:

- a Workflow attached to another business's WorkspaceDomain;
- a WorkflowRun attached to another business's Workflow or source upload;
- a WorkflowRunInput using another business's Dataset;
- a PipelineRun using another business's Dataset;
- a ModelAsset or ModelVersion crossing Workspace or Workflow boundaries;
- a candidate selected from another PipelineRun.

When an actor is supplied, the service also uses the centralized membership
authorization primitives and requires workspace write authority. IDs supplied by
callers are selectors only; they do not establish access.

## Compatibility

Migration `0023_data_model_lineage` backfills every existing Dataset into the
default Workspace with its own DatasetAsset and backfills each existing Experiment
from its Dataset's Workspace. Existing Experiments keep `workflow_run_id = NULL`,
which preserves all internal Lab routes and deterministic execution behavior.

New lineage-aware callers use `create_pipeline_run`; legacy callers may continue
using `create_experiment`. Both paths execute the same existing deterministic
engine.

Migration `0024_labs_runtime_lineage` permits the pre-execution PipelineRun shell
to have no resolved task and adds explicit failure reasons to WorkflowRun and
Experiment. On failure both rows remain persisted with terminal status and the
reason, while ModelVersion publication is omitted. Successful runs clear those
fields and publish only after the existing deterministic winner is finalized.
