---
paths:
  - "apps/api/app/agents/**"
  - "apps/api/app/engine/lab/llm_client.py"
  - "apps/api/app/engine/lab/decision_validator.py"
  - "apps/api/app/services/*decision*"
  - "apps/api/app/services/*agent*"
  - "apps/api/app/providers/**"
  - "apps/api/app/mcp/**"
---

# Agent / LLM / MCP rules

- Agents propose; deterministic services validate and execute. No agent, NOOA
  class, Jev answer or MCP tool writes product state except through the same
  service functions the HTTP API uses, with re-authorization per call.
- Every model call goes through the DCLab LLM gateway and is recorded
  (provider, model, prompt/release version, input digest, usage, latency, outcome).
- Outputs are schema-validated (Pydantic). Invalid, low-confidence or unavailable
  → abstain and use the deterministic fallback. Never crash the ML pipeline.
- Send compact evidence (profiles, aggregates, column metadata), never raw rows by
  default, never holdout labels, never secrets.
- NOOA CodeAct (generated Python) never runs in the API or ordinary worker
  process — only in the isolated sandbox lane, and only when its flag is on.
- Every integration has its own feature flag (default off) and kill switch;
  tests use deterministic fakes, never live providers.
