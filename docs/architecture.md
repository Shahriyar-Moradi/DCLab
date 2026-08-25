# Architecture

DCLab v0.1 keeps one Python package (`app` under `apps/api`) so uvicorn, Alembic, and `pip install -e .` stay unchanged.

- **M1** (`app/api/opportunities.py`, `decisions.py`, `app/ml/`, `apps/web` ledger) is preserved. Generate JSON still has eight keys.
- **Engine** (`app/engine/`) is dataset-agnostic: loaders, profiler, PIT targets, feature-group combinations, model registry, splits, leakage, metrics, diversity selection, ensemble, runner, reports.
- **Adapters** (`app/engine/datasets/olist.py`, `synthetic.py`) are the only place vendor tables are named.
- **Lab** tables (`environments`, `datasets`, `prediction_tasks`, `experiments`, `experiment_candidates`) are additive. Artifacts live on disk under `artifacts/experiments/{id}/`.
- **CLI** `dclab` and **API** `/lab/*` share `app.services.lab_service`.
