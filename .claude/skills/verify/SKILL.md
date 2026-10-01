---
name: verify
description: Run the right DCLab verification set for the current diff (backend pytest files touched/related, alembic single head, truth artifacts, web tsc/lint/tests) and summarize pass/fail. Use before saying any change is done.
argument-hint: "[quick|full]"
---

Determine the scope from `git diff --name-only` (and staged files):

- `apps/api/app/db/**` or `apps/api/alembic/**` → `.venv/bin/alembic heads` (must be 1),
  `PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests/test_database_foundation_gate.py -q`,
  `.venv/bin/python -m scripts.generate_truth_artifacts --verify-idempotent`.
- `apps/api/app/engine/**`, `services/auto_train_service.py` → `test_ml_automation_e2e.py`,
  `test_auto_train_service.py`, plus `test_e2e_lab_run.py` if present.
- Other `apps/api/**` → test files whose names match the changed module, plus
  `test_access_control.py` if routes changed.
- `apps/web/**` → from `apps/web`: `npx tsc --noEmit && npm run lint && npm run test:components`.
- `$ARGUMENTS` == `full` → `PYTHONPATH=apps/api .venv/bin/python -m pytest -q -x` and full web set.

Delegate execution to the `test-runner` subagent and report a compact table:
command → result. Never report success for a command that was not run.
