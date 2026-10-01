---
name: new-migration
description: Create a DCLab Alembic migration safely — discover the live head, write an additive expand-style revision matching db/models.py, test upgrade/downgrade on a disposable DB, regenerate truth artifacts. Use whenever models.py changes.
argument-hint: <short_snake_case_description>
---

1. `.venv/bin/alembic heads` → exactly one head; note its revision id and number.
2. Name the file `apps/api/alembic/versions/<next_number>_$ARGUMENTS.py` following the
   existing numbering pattern (look at the latest file for style: explicit
   `op.create_table`, named constraints, composite tenant FKs, indexes).
3. Write upgrade and downgrade by hand (autogenerate only as a cross-check:
   `.venv/bin/alembic revision --autogenerate` into a scratch file, then delete it).
4. Rules: additive only; new NOT NULL columns need a server default or a backfill
   step; never modify applied revisions or `alembic_frozen/`.
5. Test on the test DB: `DATABASE_URL=postgresql://postgres:postgres@localhost:5432/decisionai_test .venv/bin/alembic upgrade head`,
   then `downgrade -1`, then `upgrade head` again.
6. Run `test_database_foundation_gate.py` and
   `.venv/bin/python -m scripts.generate_truth_artifacts --verify-idempotent`; include the
   regenerated `contracts/` diff in the change.
7. Invoke the `db-migration-reviewer` subagent.
