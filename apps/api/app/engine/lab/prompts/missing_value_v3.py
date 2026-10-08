"""System prompt v3 for a per-column missing-value decision (P6.9-A): it offers only the
actions the pipeline executes — ``impute_median``, ``impute_most_frequent`` (the column's
numeric / categorical treatment) and ``drop_column`` (an exclusion, never above L1) — no
``impute_mean``, ``drop_rows`` or model-supplied ``domain_fill``.

The text lives in ``app/agents/prompts/missing_value/v3.md``; this module re-exports it.
"""

from app.agents.prompt_releases import prompt_text

AGENT_KEY = "missing_value"
PROMPT_VERSION = "missing_value_v3"
SYSTEM_PROMPT = prompt_text(AGENT_KEY, 3)
ACTIONS = ("impute_median", "impute_most_frequent", "drop_column")

__all__ = ["ACTIONS", "AGENT_KEY", "PROMPT_VERSION", "SYSTEM_PROMPT"]
