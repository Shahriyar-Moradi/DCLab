# Reporting

Each run writes `artifacts/experiments/{id}/result.json` and `report.md`.

The report includes funnel counts, leakage risk, split strategy, best single, fusion, held-out test metrics, feature-group usefulness, combination table, and warnings.

API: `GET /lab/experiments/{id}/report`.
CLI: `dclab experiment report --id ...`.
