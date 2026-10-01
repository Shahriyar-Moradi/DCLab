"""Frozen Decision.ai vertical: re-exports the engine implementation."""

from app.engine.selection import greedy_diverse_selection

__all__ = ["greedy_diverse_selection"]
