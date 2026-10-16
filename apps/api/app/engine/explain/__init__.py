"""Per-row prediction reasons (P7.7-A): which input columns moved a model's score for a row.

Reasons describe what the MODEL used, not causes in the world. See ``reasons`` for the
methods (tree SHAP, linear contributions, occlusion) and their guarantees.
"""

from app.engine.explain.reasons import (  # noqa: F401 - the package's public names
    MAX_ROWS, METHODS, REASON_COLUMNS, TOP_K, ExplainUnavailable, Explanation, FeatureMap, available_summary,
    choose_method, display_names, explain_rows, feature_map, plain_text, reason_texts, training_baselines,
    unavailable_summary,
)
