# DCLab contract snapshots (S0-P01B)

These JSON files are the mechanically verifiable public surface from
`scripts.record_repo_truth` / FastAPI / SQLAlchemy / Alembic. CI fails when
runtime facts drift from them.

| File | What it pins |
| --- | --- |
| `v1_openapi.json` | Canonical `/v1` operations, parameters, request/response schema names, SDK DTO fields |
| `openapi_operations.json` | Every public HTTP operation (`METHOD path`) |
| `sqlalchemy_tables.json` | SQLAlchemy mapped table names (PipelineRun remains `experiments`) |
| `truth_baseline.json` | Head revision, revision count, table count, OpenAPI path/operation/`/v1` counts |

## Refresh snapshots

After an **intentional** contract, schema, or `/v1` change:

```bash
.venv/bin/python -m scripts.check_truth_drift --write-snapshots
```

Commit the snapshot diff in the **same** PR as the code change. The PR
description must say whether the change is:

1. **Additive** — new path, optional field, new table via expand-and-contract
   migration. Snapshot grows; no existing client should break.
2. **Breaking** — removed or renamed operation, required field, or table.
   Needs an explicit compatibility plan; do not refresh the snapshot silently
   to hide a removal.

`python -m scripts.check_truth_drift` (no flags) is read-only.

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
equivalent). Do not use `--write-snapshots` to skip a metadata mismatch.

## Reviewer checklist

- Snapshot-only PRs with no product change are almost always accidental; reject.
- `/v1` removals in `v1_openapi.json` / `openapi_operations.json` are breaking.
- Historical verification reports stay immutable. Status lives in
  `docs/verification/README.md`, not by rewriting old evidence.
