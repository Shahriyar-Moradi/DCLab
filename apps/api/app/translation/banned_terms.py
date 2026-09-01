"""The vocabulary a client-facing surface must never contain.

This list is the enforcement mechanism for the one rule the access-split document
exists for: no raw ML-engine detail reaches a client screen, API response, or error
message. `scripts/scan_banned_terms.py` and `test_translation_layer.py` both import
from here, so this file is the single source of truth — extend it here, not in the
scanners.
"""

from __future__ import annotations

import re

# Single words/short tokens, matched with word boundaries so "training" is caught
# but "retraining_scheduled_by_customer" style compounds still hit (word boundary
# is on the substring, not the whole identifier) — see _WORD_BOUNDARY below.
BANNED_WORDS: tuple[str, ...] = (
    "model",
    "ensemble",
    "candidate",
    "auc",
    "roc",
    "precision",
    "recall",
    "calibration",
    "calibrated",
    "hyperparameter",
    "training",
    "validation",
    "leakage",
    "fusion",
    "robustness",
    "overfit",
    "overfitting",
    "underfit",
)

# Multi-word phrases and fragments that only make sense as ML jargon — matched as
# plain substrings (case-insensitive), no word-boundary needed.
BANNED_PHRASES: tuple[str, ...] = (
    "feature importance",
    "feature_importance",
    "feature group",
    "feature_group",
    "confidence score",
    "hyper-parameter",
    "best single",
    "best_single",
    "p(y)",
    "gradient boosting",
    "random forest",
    "xgboost",
    "lightgbm",
    "catboost",
    "logistic regression",
    "neural network",
    "cross-validation",
    "cross validation",
    "held-out",
    "held out",
    "pr_auc",
    "pr-auc",
)

# Client-facing copy that is allowed to use an otherwise-banned token. Masked
# before scanning so a Labs milestone can say "Building your model" without
# opening the door to "the model failed" or "model_family".
_ALLOWED_LITERALS: tuple[str, ...] = ("building your model",)

# Custom boundary instead of \b: underscores and hyphens must act as separators
# too, so "model_version" and "best-precision" are caught the same as "model" and
# "precision" on their own — \b alone treats "_" as a word character and would
# miss exactly the identifier style this codebase uses.
_WORD_PATTERN = re.compile(
    r"(?<![a-zA-Z0-9])(" + "|".join(re.escape(word) for word in BANNED_WORDS) + r")(?![a-zA-Z0-9])",
    re.IGNORECASE,
)


def find_banned_terms(text: str) -> list[str]:
    """Every banned word/phrase that appears in `text`, de-duplicated, in the
    lowercase form it was defined in — never the raw matched text, so a violation
    report can't itself leak surrounding context."""
    if not text:
        return []
    masked = text
    for literal in _ALLOWED_LITERALS:
        masked = re.sub(re.escape(literal), " ", masked, flags=re.IGNORECASE)
    hits: set[str] = set()
    for match in _WORD_PATTERN.finditer(masked):
        hits.add(match.group(1).lower())
    lowered = masked.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            hits.add(phrase)
    return sorted(hits)


def is_clean(text: str) -> bool:
    return not find_banned_terms(text)
