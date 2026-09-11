# DCLab contract snapshots (S0-P01B)

These JSON files are the canonical mechanically generated truth surface from
FastAPI, SQLAlchemy, Alembic, the SDK, and the repository inventory. Exactly one
writer owns them: `scripts.generate_truth_artifacts`. The recorder is read-only
and the drift checker never rewrites an artifact.

| File | Sole ownership |
| --- | --- |
| `v1_openapi.json` | `/v1` parameter and request/response-shape compatibility plus the OpenAPI fields consumed by SDK DTO checks |
| `openapi_operations.json` | Presence of every public HTTP operation (`METHOD path`), including `/v1` membership |
| `sqlalchemy_tables.json` | SQLAlchemy mapped table names (PipelineRun remains `experiments`) |
| `truth_baseline.json` | Alembic head/revision aggregate, OpenAPI/table aggregate counts, and repository/source/test/web inventory |
| `truth_manifest.json` | Generator name/version, SHA-256 of all non-generated non-ignored source paths and bytes, and SHA-256 digest of each artifact above |

CURRENT documentation links to these artifacts instead of owning a second copy
of their numbers. Historical reports keep their frozen measurements.

## Refresh snapshots

After an **intentional** contract, schema, or `/v1` change:

```bash
.venv/bin/python -m scripts.generate_truth_artifacts
.venv/bin/python -m scripts.generate_truth_artifacts --check
.venv/bin/python -m scripts.generate_truth_artifacts --verify-idempotent
```

The manifest `source_sha` is a SHA-256 content digest, not a Git commit ID. It
hashes the sorted paths and bytes returned by Git for tracked and non-ignored
new files, excluding the generated outputs themselves. This avoids a
self-referential commit hash while remaining identical before and after the
files' first commit.

Commit the snapshot diff in the **same** PR as the code change. The PR
description must say whether the change is:

1. **Additive** — new path, optional field, new table via expand-and-contract
   migration. Snapshot grows; no existing client should break.
2. **Breaking** — removed or renamed operation, required field, or table.
   Needs an explicit compatibility plan; do not refresh the snapshot silently
   to hide a removal.

`python -m scripts.check_truth_drift` is read-only. CI generates twice, requires
byte-identical output, requires `git diff --exit-code -- contracts/`, then runs
the drift checker. None of these steps uses the network.

`POST /auth/login` and `POST /auth/register` no longer return `access_token`
(S0-P02A). API clients must use `POST /auth/tokens`. That is a **breaking**
browser-login JSON change; snapshot refresh is intentional and documented in
`docs/adr/0001-browser-session-bff.md`.

S0-P02B adds CSRF, logout-all, and recovery **hooks** (`GET /auth/csrf`,
`POST /auth/logout-all`, password-reset and email-verification request/confirm).
Those paths are **additive**. Cookie-authenticated mutations now require
`X-CSRF-Token` and a trusted Origin.

S0-P03A adds `PUT /auth/workspace` and additive `/v1/me` fields
(`active_workspace_id`, `workspaces`, `request_id`). `GET /v1/workspaces`
list schema is unchanged. Browser session selection does not apply to
`packages/dclab_client`. Snapshot refresh is **additive**.

S0-P04A adds nullable `simulation_runs.workspace_id` / `project_id` and additive
`SimulationRunRead` fields. `GET /v1` is unchanged. Unowned historical simulation
rows are not backfilled. Snapshot refresh is **additive**.

After `alembic upgrade head` in CI, also run:

```bash
python -m scripts.check_truth_drift --alembic-check
```

That compares SQLAlchemy metadata to the migrated database (`alembic check`
equivalent). Do not regenerate artifacts to hide a metadata mismatch.

## Reviewer checklist

- Snapshot-only PRs with no product change are almost always accidental; reject.
- `/v1` operation removals in `openapi_operations.json` and shape changes in
  `v1_openapi.json` are breaking.
- Historical verification reports stay immutable. Status lives in
  `docs/verification/README.md`, not by rewriting old evidence.
