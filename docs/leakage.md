# Leakage

The detector scores columns LOW / MEDIUM / HIGH with reasons (post-outcome names, timestamps after cutoff, single-feature AUC ≥ 0.97).

High-risk columns are **not** silently deleted. With `exclude_high_leakage: true` (default) they are held out of training and still listed in the report.

PIT rule: labels use events in `(prediction_time, prediction_time + horizon]`; features use event times `≤ prediction_time`.
