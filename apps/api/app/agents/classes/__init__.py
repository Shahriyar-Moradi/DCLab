"""Specialist agent classes (P6.4-A; AGENTS_NOOA_JEV.md §3; ADR 0008 §1, §6).

Registry data, imported only by the harness (CI rule c): each module registers one
``AgentClass`` (Predict only) with its Pydantic output, context builder and deterministic
validators. Proposals are advice: stored at the decision point's level (L0 shadow until
evidence, at most L1 ``proposed``), never applied by an agent.
"""

from app.agents.classes import dataset_investigator, experiment_critic, experiment_planner  # noqa: F401
