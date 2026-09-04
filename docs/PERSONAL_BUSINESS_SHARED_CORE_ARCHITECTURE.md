# DCLab Personal Development + Business Shared-Core Architecture

## Purpose

DCLab has two customer-facing product experiences:

1. **Personal Development** — individual ML engineers, data scientists, researchers, analysts, and developers.
2. **Business** — organizations whose ML/data teams use the same ML engineering capabilities, with additional team, administration, governance, collaboration, monitoring, and commercial controls.

The critical architecture rule is:

> **DCLab Core is not the Personal Development product. Both Personal Development and Business consume the same core ML platform. Business extends the shared core; it does not fork, duplicate, or wrap the Personal product as its implementation dependency.**

The dependency direction is therefore:

```text
Personal Development  ──► DCLab Core
Business              ──► DCLab Core
Business              ──► Organization/Admin/Governance extensions
DCLab Platform Admin  ──► Core + cross-workspace operational administration
```

Never introduce `Business -> Personal Development -> Core`.

---

## Current repository assessment

The repository already contains several foundations that should be preserved:

- `Workspace` is already the canonical tenant/resource boundary.
- `WorkspaceMembership` already allows one user to belong to multiple workspaces.
- `PlatformMembership` correctly separates DCLab staff authority from customer workspace authority.
- Core dataset/workflow/run/model lineage is already workspace-scoped.
- Business administration already reads the same `MlWorkflow`, `WorkflowRun`, `Experiment`, `ModelAsset`, and `ModelVersion` records instead of maintaining a second ML store.
- The existing translated `/app` client surface and its banned-term/security tests are intentional product behavior and must not be destroyed while adding the ML-engineering product.
- The existing `/admin` platform surface, business administration surface, lineage, observability, append-only ML run events, and current regression tests are valuable and must remain compatible.

The repository also contains assumptions that no longer match the target product model:

1. Existing workspaces are implicitly treated as businesses; there is no explicit Personal vs Business workspace type.
2. `WorkspaceMembership` only has `business_admin` and `business_developer` roles.
3. `business_developer` is globally read-only for workspace mutations. That is appropriate for business administration, but wrong for an ML engineer who must be able to launch experiments, train models, debug, and create deployments without becoming a Business Admin.
4. `dclab_developer` is a DCLab **platform staff** read-only role. It must not be reused for customer Personal Development users.
5. `/app` is intentionally translated and hides raw ML details. Personal Development needs full ML engineering detail, so it must not be implemented by weakening `/app`.
6. `MlWorkflow.workspace_domain_id` is currently required, coupling every core workflow to a business-domain record. Personal ML projects need to be able to exist without a Business Domain.
7. The legacy global `users.role` field still drives UI/session compatibility. Membership rows are already authoritative in backend authorization, so new work must continue moving authority toward workspace context instead of increasing dependence on the legacy role field.

---

## Target identity model

### User

A human identity. A user can simultaneously:

- own/use a Personal workspace;
- belong to zero or more Business workspaces;
- in exceptional internal cases, also have a DCLab platform membership.

A user is not permanently classified as either a personal or business human. **The active workspace determines the customer context.**

### Workspace

`Workspace` remains the canonical boundary and gains an explicit kind:

- `personal`
- `business`

All ML resources continue to belong to a workspace.

### Membership

Membership determines authority inside a workspace. Existing business role strings are retained for compatibility.

Initial compatibility roles:

- `personal_developer` — owner/member of a Personal Development workspace; may execute shared ML-core work in that workspace.
- `business_admin` — may administer the Business workspace and execute ML-core work.
- `business_developer` — may execute ML-core work, but may not perform organization/business-administration mutations.

Longer-term, these may be normalized into generic workspace roles + capabilities, but the MVP migration should be additive and backward-compatible rather than renaming every existing role immediately.

### Platform membership

Keep unchanged:

- `dclab_admin`
- `dclab_developer`

These are DCLab staff roles and are unrelated to the Personal Development customer product.

---

## Authorization split

Do **not** use one boolean notion of “workspace write.” There are two distinct permissions:

### Workspace administration write

Examples:

- manage members;
- change Business workspace settings;
- configure business capabilities;
- perform governance/admin actions.

Allowed to Business Admin / DCLab Admin only.

Existing `can_write_workspace()` continues to mean this administrative authority for compatibility.

### Shared ML-core execution write

Examples:

- upload/version data;
- create or modify ML projects/workflows;
- launch experiments/runs;
- create features;
- train/evaluate/compare models;
- invoke the ML debugger;
- package or deploy where the plan/policy permits.

Allowed to:

- Personal Developer in their Personal workspace;
- Business Developer in an authorized Business workspace;
- Business Admin in an authorized Business workspace;
- DCLab Admin where cross-workspace support is required.

A Business Developer must therefore be able to build models without receiving Business Admin authority.

---

## Product/API surfaces

Keep the existing surfaces and add the ML engineering product without weakening their guarantees:

### `/admin/*`

DCLab platform administration. Full cross-workspace operational visibility. Existing behavior remains.

### `/app/*`

Legacy/business translated decision-intelligence client surface. Existing no-raw-ML-output rule remains intact. Personal Development must **not** be implemented by exposing raw ML details here.

### `/business/*`

Business organization/team/technical administration. Business-specific extensions live here. Existing workspace authorization and capability checks remain.

### `/development/*` (new shared ML engineering surface)

Customer ML engineering experience. It exposes full ML engineering information appropriate to an ML engineer and is usable in either:

- a Personal workspace; or
- an authorized Business workspace.

This surface consumes the same core services/models; it is not a second ML engine.

The future web experiences should follow the same separation:

- `/development/...` for the ML engineering workspace;
- `/business/...` for organization/team extensions;
- `/admin/...` for DCLab platform operations.

A Business user can navigate between the Development workspace and Business controls according to their membership/role. A Personal user sees Development only.

---

## Core resource ownership invariant

The shared core must remain workspace-scoped:

```text
Workspace
  ├── Projects
  ├── DatasetAssets / DatasetVersions
  ├── ML Workflows
  ├── Workflow Runs
  ├── Experiments
  ├── ModelAssets / ModelVersions
  ├── Evaluations / Verification
  ├── Artifacts
  ├── Deployments / Endpoints (future/current as available)
  ├── Prediction Events
  └── ML Run Events / Observability
```

No new Personal-only copies of these tables should be created. No Business-only copies should be created where the shared object already exists.

---

## Workflow/domain decoupling

`MlWorkflow` is a core ML object. Its `workspace_domain_id` must become optional.

- Business workflows may link to an enabled `WorkspaceDomain` such as Labs, Marketing, Sales, Revenue, or Customer.
- Personal Development workflows may have no business-domain link.
- Existing Business workflows keep their current links and behavior.

This is an additive nullable migration, not a destructive redesign.

---

## Personal workspace creation

When a new Personal Development account is created:

1. create the `User`;
2. create exactly one Personal `Workspace` for the account (unless an existing personal workspace is being attached deliberately);
3. create a `WorkspaceMembership` with `personal_developer`;
4. set `users.workspace_id` to the Personal workspace as a compatibility/default-workspace hint during the legacy transition;
5. do **not** create a `BusinessProfile` for the Personal workspace;
6. do not automatically seed business domains unless a later Development feature explicitly needs one.

The system must still allow that same user to later join one or more Business workspaces.

---

## Business team behavior

A Business workspace can contain multiple ML/data users. The number of seats is a subscription/entitlement concern, not a hard-coded database rule.

Example:

```text
Business Workspace: Acme
  Business Admin
  ML Engineer        -> business_developer
  ML Engineer        -> business_developer
  Data Scientist     -> business_developer
  Data Scientist     -> business_developer
  Data Scientist     -> business_developer
```

Every technical member uses the same `/development` ML-core experience against Acme's workspace. Business Admin additionally uses `/business` administration features.

---

## Compatibility rules for this migration

1. Do not rename or delete existing tables in the first migration.
2. Do not delete `users.role` or `users.workspace_id` yet.
3. Do not change existing `/app` translation guarantees.
4. Do not give `business_developer` blanket write access through the existing `require_client` guard.
5. Add a distinct ML-execution authorization primitive instead.
6. Existing `business_admin` behavior must remain unchanged.
7. Existing DCLab platform roles must remain unchanged.
8. Existing Business workspaces are backfilled as `kind=business`.
9. Existing BusinessProfile rows remain untouched.
10. Existing workflow domain links remain untouched; only the FK nullability changes.
11. Every new core query/write must remain workspace-scoped.
12. Cross-tenant object substitution must continue returning 404 after workspace filtering.

---

## Verification plan

Before considering the migration complete, verify all of the following:

- existing backend test suite remains green;
- existing static banned-term scan remains green;
- existing frontend typecheck/lint/build remains green;
- existing live admin and client crawls remain green;
- a Personal Developer can read and execute ML-core operations only in their Personal workspace;
- a Personal Developer cannot access Business administration or DCLab admin surfaces;
- a Business Developer can execute ML-core operations in an authorized Business workspace without receiving business-administration write authority;
- a Business Developer remains blocked from another Business workspace;
- a Business Admin retains all existing Business behavior and can also use shared ML-core execution;
- a user may have both a Personal workspace and one or more Business memberships;
- Personal workspace creation does not create a BusinessProfile;
- existing Business data/models/workflows/runs remain readable after the migration;
- current lineage and observability references remain intact;
- new Personal workflows can exist with `workspace_domain_id = NULL`;
- existing Business workflows keep their non-null domain references;
- the frontend recognizes the new Personal Development role without weakening middleware signature verification.

---

## Incremental implementation sequence

1. Add `Workspace.kind`, `personal_developer` compatibility identity/membership values, and nullable `MlWorkflow.workspace_domain_id` in one forward migration.
2. Update SQLAlchemy enums/models without changing existing business/platform behavior.
3. Add `can_execute_workspace_ml()` and dedicated FastAPI dependency/guard for shared ML-core execution.
4. Add Personal workspace/account creation service and CLI/test helper.
5. Extend frontend session/middleware role recognition for Personal Development.
6. Add regression tests for Personal/Business coexistence and permission separation.
7. Introduce `/development` parent router using the new shared-core authorization guard.
8. Move/add **shared core endpoints** behind `/development` by reusing existing services; do not duplicate the engine.
9. Build the Development UI against those APIs.
10. Add plan/seat entitlements later; never hard-code “5 users” into core tenancy.

---

## Definition of architectural success

The migration is correct when one real user can own a Personal Development workspace, join a Business workspace, switch context, and use the exact same ML workflow/model/run engine in both; Business adds organization capabilities on top; existing translated client and DCLab admin functionality still passes its current regression suite; and no shared ML-core table or service is duplicated for Personal vs Business.
