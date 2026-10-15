"""Stable scoring failure codes and the contract-check size bound.

The engine raises ``ScoringError(code, ...)`` with these codes, so it owns them;
``app.domain.batch_predictions`` re-exports them as ``batch_predictions.error_code``
values (and the CHECK bound). Pure constants: no app imports.
"""

from __future__ import annotations

CONTRACT_CHECK_MAX_BYTES = 65536

FEATURE_CONTRACT_FAILED = "feature_contract_failed"
UNSUPPORTED_TRANSFORM = "unsupported_transform"
UNSUPPORTED_MODEL = "unsupported_model"
SCORING_FAILED = "scoring_failed"
