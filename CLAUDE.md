# DCLab — Claude Code instructions

@AGENTS.md

## What DCLab is (current direction, 2026-10-01)

DCLab is an ML lab whose unit of work is the **versioned ML state graph**
(ProblemSpec → DatasetVersion → SplitPlan → FeatureRecipe → Experiment →
Run/Candidates → Evaluation → ModelSelection → ModelVersion → BatchPrediction →
MonitoringWindow, with ProjectDecisionRecords attached). Agents (external via
MCP, internal via NOOA proposal classes) and humans (Studio) operate on that
graph; deterministic services own every state change.

**Authority:** `docs/mvp/README.md` and `docs/mvp/ROADMAP.md` define the active
plan. The old `docs/agentic-program/` Scope 0–10 program is **paused**; read it
only as design reference when a prompt cites it.

## Commands

- Backend tests (needs local Postgres, db `decisionai_test`):
  `PYTHONPATH=apps/api .venv/bin/python -m pytest apps/api/tests/<file> -q`
- Migrations: `.venv/bin/alembic heads` (must be exactly one), `.venv/bin/alembic upgrade head`
- Truth artifacts after schema/API change:
  `.venv/bin/python -m scripts.generate_truth_artifacts --verify-idempotent`
- Web (from `apps/web`): `npx tsc --noEmit`, `npm run lint`, `npm run test:components`, `npm run build`
- Docker stack: `docker compose -f docker-compose.base44.yml up -d --build`

## Non-negotiables (short form — details in docs/mvp/ARCHITECTURE.md §3)

1. Workspace is the tenant boundary; every query on tenant data filters by workspace.
2. Evidence rows are immutable once locked; corrections create new versions.
3. Final holdout is fit/selected on never; preprocessing is fit inside folds (sklearn Pipeline).
4. LLMs/agents (OpenAI, NOOA, Jev) are advisory: typed, validated, logged in
   `llm_invocations`/agent tables, and every path works with them disabled.
5. Migrations are additive (expand → migrate → contract); discover the live head first.
6. No new parallel system for an existing concern — search before creating a
   service, table, run type, queue or training path.
7. Never claim done without running the named tests; report failures verbatim.

## Working style

- One prompt from `docs/mvp/prompts/` = one branch/PR. Use the `/run-prompt` skill.
- Prefer editing existing modules over adding new ones; keep diffs ≤ ~800 changed lines.
- Do not write status/report markdown unless the prompt asks for its evidence file.
- Use the `ml-correctness-reviewer`, `db-migration-reviewer` and `security-reviewer`
  subagents before finishing work that touches their areas.
