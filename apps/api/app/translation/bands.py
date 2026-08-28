"""Turning a raw score into the only form a client ever sees it in: a band.

These thresholds are intentionally simple and centralized — every translator uses
the same function so "High" means the same thing everywhere in the product.
"""

from __future__ import annotations

from app.translation.models import ConfidenceBand


def probability_to_band(probability: float, *, high: float = 0.7, medium: float = 0.4) -> ConfidenceBand:
    if probability >= high:
        return ConfidenceBand.HIGH
    if probability >= medium:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW


def agreement_to_band(agreement: float, *, high: float = 0.85, medium: float = 0.6) -> ConfidenceBand:
    """Member-model agreement is an internal signal, never shown as a number — it
    only ever surfaces client-side as which band it falls into."""
    if agreement >= high:
        return ConfidenceBand.HIGH
    if agreement >= medium:
        return ConfidenceBand.MEDIUM
    return ConfidenceBand.LOW
