# DclabReadinessFailing — a required readiness check fails

**Symptom.** `GET /health/ready` answers 503 (`status: not_ready`) for 5 minutes; the `check` label
names the failing check. `/health` (liveness) may still answer 200.

**Checks and causes per check.**
- `database` / `unreachable`: Postgres down, wrong `DATABASE_URL`, connections exhausted.
  `docker compose -f docker-compose.base44.yml ps postgres`;
  `docker compose -f docker-compose.base44.yml exec postgres pg_isready -U postgres`.
- `migrations` / `not_at_head`: the database is behind (or ahead of) the deployed code.
  `.venv/bin/alembic current` and `.venv/bin/alembic heads` (must be exactly one head).
  `unknown` / `not_versioned` means no `alembic_version` table (a database built without Alembic,
  e.g. tests); `scripts_unavailable` means the image has no `apps/api/alembic` directory.
- `object_storage` / `unreachable`: bucket credentials, network or the local root
  (`OBJECT_STORAGE_PROVIDER`, `OBJECT_STORAGE_BUCKET`, `OBJECT_STORAGE_ROOT`). The probe only checks
  that a key lookup succeeds; it writes nothing.

**Safe actions.**
- Database: restore connectivity first; do not restart the API in a loop (it cannot help).
- Migrations behind: run the `init` job (`alembic upgrade head`) for the deployed revision;
  migrations are additive, so the old API keeps working during the upgrade. Ahead: roll the code forward.
- Storage: fix credentials/network; uploads and run outputs fail until it is back.

**Escalation.** Database or storage down more than 15 minutes: page the backend on-call; a migration
that fails to apply: stop deploys and page the owner of the migration (`git log apps/api/alembic/versions`).
