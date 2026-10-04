# DCLab MVP program (active authority since 2026-10-01)

This folder replaces the **order** of the Scope 0–10 program in
`docs/agentic-program/`. That program is paused, not deleted: its invariants,
ADRs and designs are carried forward here and cited where they are reused.

| File | Role |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Product definition, system architecture, software/database/infrastructure fundamentals, state-graph model, keep/freeze map of the current code |
| [AGENTS_NOOA_JEV.md](AGENTS_NOOA_JEV.md) | How internal agents (NVIDIA NOOA) and typed decisions (TypeSafe Jev) fit, with schema, safety and release gates |
| [ROADMAP.md](ROADMAP.md) | Phases 0–9, plans, exit gates, assistant and R&D tracks, merge points |
| [UI_COVERAGE.md](UI_COVERAGE.md) | Every `/v1` operation and MCP tool mapped to its Studio screen (exists / planned prompt / API-only) |
| [EXECUTION_GUIDE.md](EXECUTION_GUIDE.md) | How to run a prompt with Claude Code, model selection rubric, definition of done |
| [prompts/](prompts/) | One file per phase plus [ASSISTANT.md](prompts/ASSISTANT.md) (Track A, the in-app assistant); each prompt is one commit, merged in batches at merge points |
| [../QUICKSTART_MCP.md](../QUICKSTART_MCP.md) | Connect Claude Code to DCLab over MCP (service token, `.mcp.json`, Phase 3 walk-through) |
| [STATUS.md](STATUS.md) | The only progress ledger (prompt → done/date/SHA/evidence) |

Precedence when documents disagree: current founder instruction → live code and
tests → accepted ADRs (`docs/adr/`) → this folder → `docs/agentic-program/`
(design reference) → older docs.

Next: Phase 4, Stage 1 — `/run-prompt P4.9-A` (see EXECUTION_GUIDE.md). A1-A (assistant ADR) can run in parallel.

Product focus: **an ML lab your AI agent can drive, which proves every model is
correct** — for data scientists and ML engineers on tabular data.
