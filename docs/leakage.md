# Leakage

Open-ingest Labs uses the prediction-time **LeakageAuditor** in
`apps/api/app/engine/modeling/leakage_auditor.py`. Name tokens or correlation
alone never drop a column. HIGH/CRITICAL exclusions require supporting
evidence. See [DCLAB_ADAPTIVE_MODEL_BUILDER.md](DCLAB_ADAPTIVE_MODEL_BUILDER.md).

The older detector in `apps/api/app/engine/leakage/detector.py` still scores
columns LOW / MEDIUM / HIGH with reasons (post-outcome names, timestamps after
cutoff, single-feature AUC ≥ 0.97) for non-open-ingest experiments.

High-risk columns are **not** silently deleted. With `exclude_high_leakage: true`
(default) they are held out of training and still listed in the report.

PIT rule: labels use events in `(prediction_time, prediction_time + horizon]`;
features use event times `≤ prediction_time`.
