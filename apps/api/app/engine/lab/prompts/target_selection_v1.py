"""System prompt for ranking ambiguous generic target candidates.

The text lives in ``app/agents/prompts/target_selection/v1.md`` (its digest is the gateway's
prompt release, ADR 0009 §2.8); this module only re-exports it. A wording change is
a new ``v2.md``, never an edit. Recorded decisions keep pinning ``PROMPT_VERSION``.
"""

from app.agents.prompt_releases import prompt_text

AGENT_KEY = "target_selection"
PROMPT_VERSION = "target_selection_v1"
SYSTEM_PROMPT = prompt_text(AGENT_KEY, 1)

__all__ = ["AGENT_KEY", "PROMPT_VERSION", "SYSTEM_PROMPT"]
