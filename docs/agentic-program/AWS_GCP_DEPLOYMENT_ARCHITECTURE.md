# AWS and Google Cloud deployment architecture

**Status:** canonical dual-cloud production contract

**Reviewed:** 2026-09-13

**External-compute extension:** 2026-09-15 user request; see
[`EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md`](EXTERNAL_COMPUTE_PROVIDER_ARCHITECTURE.md).
The AWS/GCP contract below governs the platform home, not an exclusive list of
permitted CPU/GPU execution providers. External adapters remain proposed/unverified.

**Product authority:** [`DCLAB_CORE_CONCEPT.md`](DCLAB_CORE_CONCEPT.md)

**Agent/ML authority:**
[`AGENT_FIRST_MVP_ARCHITECTURE.md`](AGENT_FIRST_MVP_ARCHITECTURE.md)

**Execution authority:**
[`MASTER_SCOPE_0_TO_10_PLAN.md`](MASTER_SCOPE_0_TO_10_PLAN.md) and
[`prompts/EXECUTION_STANDARD.md`](prompts/EXECUTION_STANDARD.md)

## 1. Decision

AWS and Google Cloud are equal supported deployment targets for DCLab. The
application, domain, API, SDK, CLI, agent, ML and evidence contracts remain one
product. Cloud-specific behavior exists only in reviewed infrastructure and
adapter packages.

The production MVP supports deploying the same immutable release independently
to either provider. It does not require one installation to span both clouds,
automatic AWS-to-GCP failover, active-active data replication, or simultaneous
writes to two PostgreSQL/object stores. Those capabilities would materially
increase consistency, security, cost and incident-response complexity.

An environment may additionally place approved execution on Runpod, Railway,
Lambda GPU Cloud, Vast.ai or Nebius through reviewed adapters. Its API, PostgreSQL,
queue, canonical objects, secrets and lifecycle authority remain in one AWS/GCP
home. External placement is a separate target/grant/data-transfer decision; it
does not duplicate product state or silently migrate live kernels.

Each environment has one explicit `DeploymentProfile`:

```text
provider: aws | gcp
region: approved provider region
availability_mode: multi_zone
data_residency_policy_version: immutable reference
infrastructure_release: immutable digest
application_release: identical OCI image digests
platform_contract_version: immutable reference
```

Changing provider is a controlled restore/migration and DNS cutover, not a
runtime configuration flip. Here, provider means the platform home; choosing an
approved execution target does not change it. No domain row, public API response, project
decision, model package or dataset identity depends on an ARN, Google resource
name, bucket URL, cloud account/project number or provider SDK object.

## 2. Common substrate and provider mapping

Use pinned OpenTofu-compatible HCL for infrastructure, one shared Kubernetes
base and small provider overlays. Scope 9 must pin the OpenTofu CLI, AWS and
Google providers, Kubernetes version, Kustomize version and every module source
by version/digest. Provider modules may differ internally but must implement the
same reviewed inputs, outputs, policies and conformance tests.

| Capability | AWS implementation | Google Cloud implementation | Portable DCLab boundary |
| --- | --- | --- | --- |
| Environment isolation | Separate AWS accounts for staging/production | Separate GCP projects for staging/production | `DeploymentProfile`; no shared production identity/state |
| Containers | Amazon EKS with managed node groups | GKE Standard with managed node pools | Same OCI image digest, Kubernetes base and health contract |
| Registry | Amazon ECR | Artifact Registry | Digest-pinned `ImageReference`; no mutable production tags |
| Workload identity | EKS Pod Identity for supported node-backed pods; IRSA where Fargate requires it | Workload Identity Federation for GKE | One Kubernetes service account per workload and no static cloud key |
| Product PostgreSQL | Amazon RDS for PostgreSQL Multi-AZ | Cloud SQL for PostgreSQL HA | Standard supported PostgreSQL version/schema/Alembic contract |
| MLflow PostgreSQL | Separate RDS database/schema and identity | Separate Cloud SQL database/schema and identity | Same private MLflow image and DCLab reconciliation contract |
| Object storage | Private Amazon S3 buckets | Private Google Cloud Storage buckets | Existing `ObjectStorage` port, opaque object version and verified digest |
| Secret storage | AWS Secrets Manager | Google Secret Manager | Versioned `SecretRef`; values never enter IaC state, images or public config |
| Key management | AWS KMS | Cloud KMS | Opaque `KeyRef`, purpose/region policy and envelope-encryption contract |
| Public edge | AWS load balancer, WAF, managed certificate and DNS modules | Google Cloud external application load balancer, Cloud Armor, managed certificate and DNS modules | One HTTPS/proxy/header/rate-limit contract |
| Private networking | VPC, private subnets/endpoints and controlled NAT/egress | VPC, private service access/connectivity and controlled Cloud NAT/egress | Deny-public data plane and code-owned egress policy |
| Operations telemetry | OpenTelemetry collector to CloudWatch/X-Ray where approved | OpenTelemetry collector to Cloud Logging/Monitoring/Trace where approved | Same metric names, trace attributes, redaction and SLO calculations |
| Audit/config evidence | CloudTrail/config/security services where approved | Cloud Audit Logs/asset/security services where approved | Normalized immutable deployment/security evidence |
| Cost controls | AWS budgets/tags | GCP budgets/labels | Same owner/environment/workload/cost-center taxonomy and normalized alerts |

RDS PostgreSQL and Cloud SQL PostgreSQL are the baseline. Aurora-specific,
AlloyDB-specific or provider-only PostgreSQL features cannot become required
product semantics. They require a later measured ADR and a standard-PostgreSQL
fallback or an explicit end to portability.

## 3. Code and configuration portability rules

1. Business/domain/application modules cannot import `boto3`, Google Cloud
   clients, Kubernetes clients or cloud-specific identity libraries.
2. Cloud SDKs live only in adapter packages and only in the workload image that
   uses them. Existing S3 and GCS storage adapters remain implementations of one
   object-storage contract.
3. Configuration uses provider-neutral typed fields and opaque references.
   Provider-specific resource identifiers stay in deployment configuration or
   private adapter state and are redacted from logs and public errors.
4. Applications use default short-lived workload credential chains. Static AWS
   access keys and GCP service-account key files are prohibited in deployed
   workloads and CI.
   External vendors that require API keys use restricted, rotated credentials
   held only by the trusted provisioner in the home secret manager. That exception
   is not permission for static AWS/GCP keys or credentials in remote user code.
5. Signed URLs, object versions, conditional writes, retention and deletion
   have normalized DCLab semantics. Each adapter must fail closed when its cloud
   cannot satisfy a requested precondition; it cannot silently weaken it.
6. The PostgreSQL schema, migrations and transaction semantics are identical.
   Provider-native authentication/proxying is infrastructure detail behind the
   connection/secret boundary.
7. Durable job intent remains in DCLab PostgreSQL for the MVP. SQS and Pub/Sub
   are not independently introduced. A future broker must implement one
   provider-neutral delivery contract and pass duplicate, reorder, cancel and
   recovery parity tests before replacing any path.
8. Schedules remain DCLab schedule/job intent. EventBridge or Cloud Scheduler
   may only send an idempotent wake-up hint; neither becomes workflow truth.
9. OpenTelemetry is the canonical instrumentation layer. Cloud-native metric,
   log and trace names do not appear in product behavior or alert semantics.
10. Feature flags and kill switches are DCLab configuration, not provider-only
    controls. Both clouds must support equivalent disable, drain and rollback.

## 4. Kubernetes workload contract

EKS and GKE deploy the same base workload families:

- web and BFF;
- API;
- deterministic ML/data workers;
- LangGraph worker-agent;
- Deep Investigation worker;
- optional OpenAI Agents adapter worker;
- connector worker;
- notebook control plane;
- isolated Python execution jobs;
- hosted MCP;
- private MLflow and reconciliation jobs; and
- migration/maintenance jobs.

Every family has its own Kubernetes service account, cloud identity, namespace
or equivalent policy boundary, handler allowlist, network policy, resources,
autoscaling policy, disruption budget where applicable, health contract and
configuration schema. Pods run non-root, drop capabilities, use read-only root
filesystems where possible, prohibit privilege escalation and host namespaces,
and do not mount the default service-account token unless required.

Kubernetes is a scheduling/isolation substrate, not DCLab product state. Do not
use ConfigMaps, Secrets, CRDs, labels or Job history as the only durable record
of a DCLab command, approval, AgentRun, SyncRun, notebook run or model build.

## 5. Isolated Python and agent sandbox parity

Unknown or user/agent-generated Python uses `SandboxRuntimePort`. The security
contract is identical even though the provider mechanism may differ:

- AWS: an approved EKS Fargate or equivalently isolated execution profile, with
  one pod/VM boundary per task where the threat model requires it;
- GCP: GKE Sandbox/gVisor on dedicated sandbox nodes; and
- both: no service-account token, product DB, node/metadata credential, host
  mount, privileged mode, unrestricted egress or shared writable volume.

The sandbox adapter must enforce immutable input/output manifests, image digest,
non-root UID, dropped capabilities, CPU/memory/ephemeral-disk/process/time/output
limits, DNS and egress allowlists, cancellation, cleanup, quarantine and audit.
Provider availability is checked before accepting a run. If the configured
cloud cannot provide the verified isolation profile, arbitrary Python stays
disabled; it must never fall back to an ordinary application worker.

External execution must satisfy the same required capability profile. A vendor
container, sandbox or “Secure Cloud” label does not establish equivalence by
itself. A provider may qualify for reviewed-code batch work but not untrusted
Python. Kernel protocol, data transfer and cleanup adapters may differ; PostgreSQL
and DCLab scientific/authorization ownership do not.

## 6. Data, backup and cross-cloud recovery

Each production deployment uses multi-zone managed PostgreSQL and private
versioned/retention-aware object storage in one approved region. RTO, RPO,
backup retention and residency are configuration policy, not provider defaults.
PITR and object recovery are exercised on both providers.

Cross-cloud recovery uses a documented, offline/controlled process:

1. quiesce or place the source in read-only maintenance mode;
2. capture and verify an application-consistent standard PostgreSQL logical
   export plus schema/Alembic head and row-count/digest evidence;
3. copy immutable object versions through a digest-verified manifest;
4. provision fresh destination secrets and keys—never export KMS keys or secret
   plaintext as part of the backup;
5. restore standard PostgreSQL and objects into an isolated destination;
6. map only private deployment references while preserving DCLab IDs, versions,
   content digests and lineage;
7. run migration, authorization, object, MLflow, agent/checkpoint, connector and
   model-package reconciliation;
8. execute read-only and then write canaries; and
9. perform an approved DNS cutover with a reversal window.

Automatic cross-cloud replication/failover, shared KMS material and dual-writer
PostgreSQL are non-goals for the MVP. A restore drill establishes portability;
it is not evidence of zero-RPO automatic disaster recovery.

## 7. CI/CD and parity evidence

PR CI is network-free and must run:

- application, API, SDK, CLI, agent, ML and browser suites once against the
  provider-neutral contract;
- S3 and GCS storage adapter conformance with fakes/emulators and malicious
  object/version/precondition cases;
- OpenTofu format/validate plus provider-lock and module-source verification;
- AWS and GCP plan/policy assertions with no apply;
- Kubernetes base plus both overlay renders, schema validation and security
  policy checks;
- image, SBOM, signature and dependency-boundary checks; and
- static rejection of provider SDK imports outside approved adapters.

Protected live pipelines use short-lived CI federation and create disposable or
dedicated staging targets in both clouds. Before a provider is advertised as
supported, the same release digest must pass the same golden-path, tenant,
security, failure, restore, load, cost and rollback suite there. Provider-
specific exceptions require an ADR, explicit capability status and user-visible
limitation; “works on one cloud” is not dual-cloud evidence.

Release states are independent: `AWS_READY`, `GCP_READY`, or both. A failure in
one provider blocks only that provider's release unless it exposes a portable
application defect. It must not force unsafe changes into the other provider.

## 8. Scope review rule

Every implementation prompt in Scopes 0–10 must answer, in its implementation
packet:

- Does it introduce an AWS/GCP assumption or provider identifier?
- Which provider-neutral port/config/resource owns the behavior?
- Which AWS and GCP implementations or “not applicable” evidence exist?
- Are security, tenancy, limits, error semantics, metrics and rollback equal?
- Which offline contract tests and live staging checks prove parity?
- Can one provider be disabled or removed without changing domain/public data?
- Does cross-cloud restore preserve IDs, versions, digests and evidence?

No feature may be marked production-MVP complete when it needs a cloud resource
and only one selected provider has an implementation or release-gate result.

## 9. Explicit non-goals

- no lowest-common-denominator abstraction that hides meaningful security or
  consistency differences;
- no cloud SDK in domain/application/public client packages;
- no active-active cross-cloud product database;
- no simultaneous dual-cloud authoritative product write path; scoped external
  input/output staging is governed by the external-compute transfer contract;
- no SQS-plus-Pub/Sub duplication while PostgreSQL jobs meet the SLO;
- no provider-native agent, ML, workflow or feature-store control plane that
  bypasses DCLab;
- no provider-specific ID or signed URL as canonical product identity; and
- no production apply, account/project creation, data movement or DNS cutover
  without its dedicated approved execution prompt.

## 10. Official references reviewed

- [Amazon EKS Pod Identity](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [Amazon EKS security guidance](https://docs.aws.amazon.com/eks/latest/best-practices/security.html)
- [Amazon EKS on Fargate](https://docs.aws.amazon.com/eks/latest/userguide/fargate.html)
- [Amazon RDS PostgreSQL point-in-time recovery](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_PIT.MultiAZDBCluster.html)
- [Amazon S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- [AWS Secrets Manager best practices](https://docs.aws.amazon.com/secretsmanager/latest/userguide/best-practices.html)
- [Workload Identity Federation for GKE](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/workload-identity)
- [GKE Sandbox](https://docs.cloud.google.com/kubernetes-engine/docs/concepts/sandbox-pods)
- [Cloud SQL PostgreSQL high availability](https://docs.cloud.google.com/sql/docs/postgres/high-availability)
- [Cloud SQL PostgreSQL PITR](https://docs.cloud.google.com/sql/docs/postgres/backup-recovery/configure-pitr)
- [Cloud Storage versioning](https://docs.cloud.google.com/storage/docs/object-versioning)
- [Cloud Storage Object Retention Lock](https://docs.cloud.google.com/storage/docs/object-lock)
- [Google Secret Manager best practices](https://docs.cloud.google.com/secret-manager/docs/best-practices)
- [OpenTofu remote state](https://opentofu.org/docs/language/state/remote/)
- [OpenTofu state and plan encryption](https://opentofu.org/docs/language/state/encryption/)

Re-check current service availability, region support, quotas, Kubernetes
versions and provider/module compatibility when S9-P01A executes; this document
defines ownership and acceptance, not a permanently valid service-version list.
