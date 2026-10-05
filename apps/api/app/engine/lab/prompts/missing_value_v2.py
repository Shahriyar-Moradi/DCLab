"""System prompt v2 for a per-column missing-value decision: no steering to evidence the
data-exposure policy withholds (raw values never reach the model, ADR 0009 §8).

The text lives in ``app/agents/prompts/missing_value/v2.md``; this module only re-exports it.
The v1 module stays because released prompt rows are immutable and its action enum is
the validator's allowlist.
"""

from app.agents.prompt_releases import prompt_text

AGENT_KEY = "missing_value"
PROMPT_VERSION = "missing_value_v2"
SYSTEM_PROMPT = prompt_text(AGENT_KEY, 2)

__all__ = ["AGENT_KEY", "PROMPT_VERSION", "SYSTEM_PROMPT"]
