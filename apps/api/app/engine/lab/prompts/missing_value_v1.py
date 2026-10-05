"""System prompt for a per-column missing-value decision.

The text lives in ``app/agents/prompts/missing_value/v1.md`` (its digest is the gateway's
prompt release, ADR 0009 §2.8); this module only re-exports it. A wording change is
a new ``v2.md``, never an edit. Recorded decisions keep pinning ``PROMPT_VERSION``.
"""

from app.agents.prompt_releases import prompt_text

AGENT_KEY = "missing_value"
PROMPT_VERSION = "missing_value_v1"
SYSTEM_PROMPT = prompt_text(AGENT_KEY, 1)

__all__ = ["AGENT_KEY", "PROMPT_VERSION", "SYSTEM_PROMPT"]
