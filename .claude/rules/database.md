---
paths:
  - "apps/api/app/db/**"
  - "apps/api/alembic/**"
---

# Database rules

- Run `.venv/bin/alembic heads` first; build on the single live head. Never edit
  an applied migration and never touch `apps/api/alembic_frozen/`.
- Additive only (expand → backfill → contract in a later PR). Destructive change
  needs an ADR in `docs/adr/`.
- Tenant-owned tables carry `workspace_id` and use composite FKs
  `(workspace_id, id)` to parents so cross-tenant references are impossible in SQL.
- State-graph nodes are immutable versions: new row per change, `parent_id` /
  `supersedes_id` for lineage, `content_digest` where a body exists. Large bodies go to
  object storage via `app/storage`, never into Postgres or `REPO_ROOT/data`.
- Append-only tables (events, invocations, decision records) get no UPDATE/DELETE paths.
- Keep `db/models.py` the single metadata owner; after changes regenerate truth
  artifacts and run the migration test (`test_database_foundation_gate.py`).
- Run the `db-migration-reviewer` subagent before finishing.
