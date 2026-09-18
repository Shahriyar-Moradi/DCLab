# ML platform integration architecture

**Status:** approved roadmap architecture; bounded DataScan implementation
begins in Plan 0.9 and the remaining ML-platform adapters begin in Plan 3.0

**Product authority:** [`DCLAB_CORE_CONCEPT.md`](DCLAB_CORE_CONCEPT.md)

**Agent/data-integration authority:**
[`AGENT_FIRST_MVP_ARCHITECTURE.md`](AGENT_FIRST_MVP_ARCHITECTURE.md). This
document owns ML execution-library boundaries; the linked decision owns `dlt`,
Snowflake/Airbyte interoperability and agent-runtime selection.

**Deployment authority:**
[`AWS_GCP_DEPLOYMENT_ARCHITECTURE.md`](AWS_GCP_DEPLOYMENT_ARCHITECTURE.md).
MLflow, PostgreSQL, object storage, workers and telemetry must satisfy the same
private adapter contract on AWS and GCP.

**Execution authority:** [`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md),
[`prompts/EXECUTION_STANDARD.md`](prompts/EXECUTION_STANDARD.md),
[`prompts/SCOPE_00_FOUNDATION.md`](prompts/SCOPE_00_FOUNDATION.md) and
[`prompts/SCOPE_03_CONTROLLED_COMMANDS.md`](prompts/SCOPE_03_CONTROLLED_COMMANDS.md)

## 1. Decision

DCLab reuses mature libraries for technical mechanics and remains the only
authority for product meaning. The production MVP adopts:

- **MLflow Tracking** for detailed training-run parameters, metrics, plots and
  artifact/model-package references;
- the **MLflow Model format** plus safe library-native model formats for model
  signature and environment/package metadata;
- **Pandera** as the execution-time dataframe validator generated from a
  DCLab-owned `FeatureContract`;
- **Evidently** as an in-process calculator for versioned reference/current
  monitoring reports;
- **skops** for supported scikit-learn persistence and reviewed native formats
  for CatBoost, XGBoost and LightGBM;
- **OpenTelemetry** for vendor-neutral application traces and metrics, exported
  to the selected Prometheus/Grafana-compatible operations stack.
- **DuckDB** as the single in-process engine behind a DCLab-owned `DataScanPort`
  for bounded schema/profile, Parquet/Arrow slice, leakage-signal and monitoring-
  window preparation work. pandas remains the modeling-frame compatibility
  layer; PyArrow is the batch interchange. Polars is not added for the MVP.

Weights & Biases is not a production-MVP dependency. It may be evaluated later
as a one-way customer export adapter, never alongside MLflow as a second owner
of the same run. Optuna and OpenLineage are deferred, measured extensions:
Optuna may supply bounded search algorithms after deterministic baselines are
proven; OpenLineage may export canonical DCLab lineage events without becoming
the lifecycle store.

SHAP is not installed or exposed in the production MVP. Existing enum values,
visualization labels or historical artifacts do not constitute an implemented
SHAP capability. Initial model understanding uses the DCLab-owned metric,
calibration, confusion, threshold, residual/error-slice, feature-statistic and
carefully labeled native/permutation-importance contracts.

Official capability references:

- [MLflow Tracking](https://mlflow.org/docs/latest/ml/tracking)
- [MLflow Models](https://mlflow.org/docs/latest/model)
- [Pandera dataframe schemas](https://pandera.readthedocs.io/en/latest/dataframe_schemas.html)
- [Evidently reports](https://docs.evidentlyai.com/docs/library/report)
- [skops secure persistence](https://skops.readthedocs.io/en/stable/persistence.html)
- [OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/)
- [DuckDB Python API](https://duckdb.org/docs/stable/clients/python/overview)
- [DuckDB security guidance](https://duckdb.org/docs/stable/operations_manual/securing_duckdb/overview)
- [W&B self-managed requirements](https://docs.wandb.ai/platform/hosting/self-managed/requirements)
- [Optuna optimization algorithms](https://optuna.readthedocs.io/en/stable/tutorial/10_key_features/003_efficient_optimization_algorithms.html)
- [OpenLineage Python client](https://openlineage.io/docs/next/client/python)

## 2. One owner per concern

| Concern | Authoritative owner | Reused implementation | Forbidden overlap |
| --- | --- | --- | --- |
| Workspace/project identity and access | DCLab PostgreSQL and authorization services | none | MLflow/W&B tags or UI permissions are never authorization. |
| Scientific objective and constraints | DCLab `ProblemSpec`, plan and decision records | none | External run tags cannot change target, metric, split or holdout policy. |
| Lifecycle lineage and project memory | DCLab immutable ML lifecycle and `ProjectDecisionRecord` | optional future OpenLineage export | No external graph, run history or agent memory becomes product truth. |
| Detailed training telemetry | MLflow Tracking | private MLflow service | DCLab stores only canonical lifecycle state, final typed metrics/evidence and the external reference needed by the product. |
| Model approval, registration, promotion and rollback | DCLab `ModelVersion`/`ModelRelease` services | MLflow Model package metadata | MLflow Registry/UI cannot promote a DCLab release or bypass approval. |
| Feature input contract | versioned DCLab `FeatureContract` | generated Pandera schema | A hand-maintained Pandera schema cannot compete with the product contract. |
| Drift/statistical calculations | DCLab monitoring policy and `MonitoringWindow` | Evidently library | Evidently thresholds/UI/storage are not alert or remediation authority. |
| Operational telemetry | DCLab telemetry policy | OpenTelemetry plus selected backend | MLflow experiment metrics are not service SLOs or security audit. |
| Search policy and budget | DCLab experiment plan and budget ledger | optional future Optuna sampler/pruner | The optimizer cannot invent objectives, candidate families, data access or unbounded trials. |
| Bounded tabular analytical scans | DCLab `DataScanPort`, authorization and code-owned template registry | ephemeral DuckDB over an authorized file or Arrow batches | DuckDB is not product storage, a public SQL endpoint, a scientific-policy owner or a direct object/network client. |

## 3. Integration topology

```text
Web / public SDK / CLI / agents
  -> DCLab /v1 and application services
       -> DCLab PostgreSQL: canonical lifecycle, decisions, policy and audit
       -> ArtifactService: immutable object metadata, digest and authorized URI
       -> DataScanPort: ephemeral DuckDB over one authorized artifact/Arrow input
       -> MlflowTrackingAdapter: detailed run telemetry and model package
       -> PanderaFeatureValidator: generated execution-time feature checks
       -> EvidentlyDriftCalculator: bounded versioned report calculation
       -> OpenTelemetry: redacted operational traces and metrics
```

No browser, public SDK, MCP server, raw LangGraph node, Deep Agents worker or
model-visible tool receives an MLflow, object-store, observability-backend or
Evidently credential. Agents read audience-safe DCLab projections and cite
DCLab resource IDs/versions. A request for external detail is resolved by a
DCLab application query after normal tenant and data-policy authorization.

## 4. Bounded DataScanPort and DuckDB

The application authorizes and resolves a concrete Dataset/Artifact version
before analytics begin:

```text
Authorized DatasetArtifact
    -> DCLab DataScanPort
    -> ephemeral DuckDB connection
    -> bounded Arrow RecordBatchReader
    -> deterministic DCLab profile/leakage/comparison/monitoring services
```

- `DataScanPort` accepts provider-neutral typed requests containing exact
  workspace/project/Dataset/Artifact/version/digests, a code-owned template key
  and version, projection, typed predicates/aggregations, deterministic sample
  policy, deadline/cancellation and hard input/output/resource bounds.
- No public, SDK, CLI, MCP, notebook or agent field accepts SQL, DuckDB
  expressions, paths, globs, URIs, extensions or database names. Managed
  notebook queries use an allowlisted relational grammar compiled by DCLab into
  the same static template catalog.
- Reuse `dataset_materialization.materialize_dataset` and ArtifactService so
  authorization, digest verification and object-store access happen before the
  adapter. DuckDB receives one exact read-only local file or registered Arrow
  object; it receives no cloud/database credential and never queries DCLab
  PostgreSQL, S3/GCS or HTTP directly.
- Each call uses `duckdb.connect(":memory:")`. Disable extension auto-install/
  autoload, community/unsigned extensions and unrestricted external access;
  for file scans restrict `allowed_paths` to the exact verified per-job input/
  temp directory, while registered Arrow inputs disable external access. Set
  `autoinstall_known_extensions=false`, `autoload_known_extensions=false`,
  `allow_community_extensions=false`, `allow_unsigned_extensions=false` and
  `lock_configuration=true` after configuration. Enforce memory/thread/temp/
  time/output limits and close/delete bounded state on every terminal path.
  Container/network policy remains the primary isolation boundary.
- Only registry-owned parameterized `SELECT`/aggregate templates execute.
  Reject `ATTACH`, `COPY`, `INSTALL`, `LOAD`, `PRAGMA`, arbitrary functions and
  every unregistered statement. DuckDB documents that untrusted SQL should be
  treated like other executable code, so configuration is defense in depth—not
  permission to expose SQL.
- Initial templates cover schema, profile, dataset slice, comparison slice,
  leakage-candidate statistics, reference/current monitoring preparation and
  bounded managed-notebook queries. Results stream as bounded PyArrow batches or
  normalized DCLab summaries; DuckDB objects never cross an application port.
- Existing deterministic services retain meaning. The profiler owns the output
  schema; the leakage auditor decides leakage/exclusion; experiment services own
  comparison and winner selection; MonitoringWindow policy/Evidently decide
  monitoring state. DuckDB only calculates authorized neutral inputs.
- Leakage templates can see only the already locked training partition and may
  never access the final holdout. Drift preparation binds immutable reference/
  current versions and a FeatureContract. No automatic fallback may move an
  oversized failed scan into an unbounded pandas load.
- pandas remains the training and small/in-memory compatibility path. PyArrow is
  the batch boundary. Polars is deferred until production benchmarks prove that
  DuckDB + Arrow + pandas cannot meet a named workload.

Activation is parity-gated. Discrete profile facts must match exactly; floating
statistics use documented finite normalization/tolerance. DuckDB becomes the
configured production scanner only after frozen scientific tests and measured
memory/latency evidence pass. The pandas adapter remains a bounded rollback for
supported small workloads, not a silent OOM fallback.

## 5. MLflow boundary

MLflow is a private internal dependency, not a second DCLab application.

- Add a narrow `ExperimentTrackingPort` owned by DCLab and one
  `MlflowTrackingAdapter`; do not pass MLflow objects through domain, `/v1`, SDK,
  CLI, UI, job or agent contracts.
- Use a separate PostgreSQL database or schema and database identity for MLflow;
  DCLab Alembic never migrates MLflow tables and no cross-database foreign key
  is created.
- Use the selected managed object store with a dedicated bucket or prefix,
  encryption policy and workload identity. DCLab stores immutable artifact
  IDs, digests and authorized logical references, not duplicated model bytes.
- Link a DCLab run to a provider-neutral external reference containing provider,
  external run/model ID, schema version, observed status, synchronization time
  and content digest. Enforce one active mapping for a DCLab run/provider.
- Attach DCLab workspace/project/run/version IDs as internal correlation tags,
  but never trust those tags for access control.
- Keep MLflow private to workers/operators. Customer-facing comparison,
  promotion and model-registry experiences remain DCLab routes and UI.
- Do not enable MLflow-side product promotion, deployment, webhooks back into
  product commands, public sharing or arbitrary artifact execution.

### Failure and reconciliation

External tracking is outside the DCLab database transaction. Training intent,
job state and scientific evidence remain recoverable when MLflow is unavailable.

1. Persist canonical DCLab intent and job idempotently.
2. Preflight the configured tracker before expensive work.
3. Create or resolve the MLflow run using the DCLab run UUID as the idempotency
   correlation value; never create a second external run on retry.
4. Stream detailed telemetry where available and always persist the bounded
   final metric/evidence summary required by DCLab.
5. On timeout or ambiguous response, mark the external reference
   `tracking_degraded` and enqueue one bounded reconciliation path.
6. A run with required tracking evidence missing may be inspected, but cannot
   become `verified`, be promoted or produce an active release.
7. Reconciliation may attach/recover the external reference; it cannot rewrite
   immutable scientific results. Operator repair is explicit and audited.

Local development may use a disabled/no-op adapter or a local MLflow profile.
Production model promotion requires configured private tracking, TLS/workload
authentication, retention, backup and restore evidence. There is no silent
filesystem `mlruns` fallback in production.

## 6. Feature-contract validation

DCLab stores the versioned feature contract: ordered feature names, logical and
physical types, nullable/default/category/unknown handling, prediction-time
availability, target exclusion, transformations, lineage, compatibility mode
and digest. `PanderaFeatureValidator` compiles that contract into a strict
runtime schema for supported pandas/Arrow inputs.

- Compilation is deterministic and its schema digest is recorded with the
  model package.
- Validation never silently drops unexpected columns, coerces unsafe types or
  fills missing required values unless that exact behavior is in the contract.
- Validation returns DCLab-owned reason codes and bounded field errors; Pandera
  exception bodies do not cross public boundaries.
- DCLab deterministic leakage, prediction-moment, group/temporal split and
  scientific checks remain separate authorities. Pandera does not replace them.

## 7. Model package and load policy

The package manifest binds model, preprocessor, feature contract, training
dataset/version, code snapshot, environment lock, library/format versions,
metrics, MLflow run/model reference and digests.

- Use `skops` only for supported scikit-learn objects after inspecting and
  allowlisting required types.
- Use reviewed native, non-Python-executable formats for CatBoost, XGBoost and
  LightGBM when supported by the selected batch path.
- Treat `pickle`, `joblib` and cloudpickle packages as legacy/quarantined unless
  a separately approved trusted-only compatibility path loads a digest-pinned
  internally produced artifact inside an isolated worker.
- Never dynamically import arbitrary code from a model package. Verify format,
  MIME, size, digest, signature, library compatibility and feature-contract
  digest before loading.
- The API process never loads a model. The bounded batch worker loads only a
  DCLab-approved package and writes immutable output through ArtifactService.

## 8. Monitoring and observability

`EvidentlyDriftCalculator` receives authorized, bounded reference/current data
projections plus a DCLab-owned method/threshold policy. It returns a versioned,
typed report. DCLab persists normalized metrics and the detailed report artifact,
decides state, emits alerts and offers an explicit investigation action. No
Evidently service, database or customer-facing dashboard is deployed in the MVP.

OpenTelemetry carries bounded trace/request/job/run correlation. Prometheus
metrics use low-cardinality labels; workspace/user/dataset/model UUIDs stay in
authorized traces or logs as hashes/references, never metric labels. Structured
logs remain redacted. DCLab audit events continue to record who changed or
approved product state; telemetry cannot substitute for audit evidence.

## 9. Configuration and release controls

Typed settings use safe defaults:

```text
DCLAB_MLFLOW_TRACKING_ENABLED=false
DCLAB_MLFLOW_TRACKING_URI=
DCLAB_MLFLOW_REQUIRED_FOR_PROMOTION=true
DCLAB_EVIDENTLY_ENABLED=false
DCLAB_OTEL_ENABLED=false
DCLAB_DATA_SCAN_ENGINE=pandas
DCLAB_DUCKDB_ENABLED=false
DCLAB_DUCKDB_MEMORY_LIMIT_MB=512
DCLAB_DUCKDB_THREADS=2
DCLAB_DUCKDB_TIMEOUT_SECONDS=30
DCLAB_DUCKDB_MAX_INPUT_BYTES=536870912
DCLAB_DUCKDB_MAX_OUTPUT_ROWS=10000
DCLAB_DUCKDB_MAX_OUTPUT_BYTES=16777216
DCLAB_DUCKDB_MAX_TEMP_BYTES=1073741824
DCLAB_DUCKDB_MAX_COLUMNS=512
DCLAB_DUCKDB_MAX_GROUPS=1000
DCLAB_DUCKDB_ARROW_BATCH_ROWS=4096
```

Production fails closed when an enabled integration lacks an allowlisted
private endpoint, authentication, timeout, retry ceiling, maximum payload,
retention or TLS policy. Dependencies are pinned in optional worker extras and
locked images; MLflow/Evidently/Pandera/skops packages do not enter the API,
public SDK, MCP or agent-worker dependency graph unless a prompt demonstrates a
runtime need and preserves the boundary. Each adapter has an independent kill
switch, health state, contract tests, metrics and rollback procedure.

## 10. Deferred tools

- **Weights & Biases:** future one-way export/import-reference adapter only for
  a customer requirement. It cannot be enabled simultaneously with MLflow for
  the same DCLab run and never owns product promotion or tenancy.
- **Optuna:** evaluate after bounded baseline candidates are correct. Use a
  code-owned search space, deterministic seed where applicable, maximum
  trials/time/cost and DCLab persistence. No separate dashboard/service first.
  The existing `optuna` value in the scientific-plane source enum is a dormant
  compatibility marker only; preserve it, but do not treat it as an installed
  optimizer, an approved execution path or provider authority.
- **OpenLineage:** emit after canonical DCLab commit through a versioned adapter;
  do not deploy Marquez or accept inbound lifecycle mutation for the MVP.
- **Polars:** do not add beside DuckDB + Arrow + pandas for the MVP. Consider one
  DataFrameEngine adapter only after a reproducible production benchmark shows a
  named transformation workload cannot meet its resource/latency target.
- **Feast, Airflow, Dagster, Prefect, Ray, Spark, Kubeflow, KServe and BentoML:**
  defer until measured scale or an online-serving requirement proves that the
  existing DCLab lifecycle, PostgreSQL jobs and bounded batch worker are
  insufficient.

## 11. Scope placement

- Scope 0 records this owner/adapter decision, implements and parity-gates the
  bounded DataScanPort/DuckDB adapter in Plan 0.9, and prevents accidental
  platform duplication; it does not deploy MLflow.
- Scopes 1–2 expose only DCLab lifecycle and evidence projections to agents.
- Plan 3.0 reuses the verified DataScanPort and implements the MLflow adapter,
  package policy, Pandera compiler/validator and Evidently calculator before
  controlled builds.
- Plans 3.1–3.8 consume those ports for model build, release, batch inference
  and monitoring. External tracking failure cannot weaken command safety.
- Scope 4 managed query/profile cells use the same typed DataScan templates and
  cite DCLab/MLflow-linked resources but receive no SQL or external credential.
- Scopes 5–6 keep framework/provider types private from `/v1`, SDK, CLI and MCP.
- Scope 7 routes supported published-dataset profiling through DataScanPort; it
  may add data connectors but does not add a second scan engine or tracker.
- Scope 9 deploys, observes, backs up and drills the private MLflow boundary and
  in-process validation/monitoring libraries on both AWS and GCP using identical
  application/image contracts; RDS/Cloud SQL and S3/GCS stay behind the approved
  database/object boundaries.
- Scope 10 may evaluate Optuna, OpenLineage or W&B only from measured demand and
  with one owner per concern.

## 12. Non-goals

This decision does not introduce a second customer-facing ML platform, replace
DCLab's registry/lifecycle/decision memory, expose vendor objects publicly,
deploy Kubernetes merely for an external tool, add online serving, add a feature
store, permit arbitrary model code, or make any external system an agent tool or
authorization authority. It also does not create a persistent DuckDB database,
user/agent SQL interface, direct DuckDB object-store/PostgreSQL integration or a
second DataFrame engine. It does not add SHAP computation, storage, jobs, APIs
or UI to the production MVP.
