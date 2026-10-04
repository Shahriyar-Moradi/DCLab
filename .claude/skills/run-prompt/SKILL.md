---
name: run-prompt
description: Execute one DCLab MVP implementation prompt (e.g. P0.2-A) from docs/mvp/prompts/ end to end — inspect, packet, implement, verify, review, report. Use when the user says "run prompt X", "implement P1.3-B", or "do the next prompt".
argument-hint: <prompt-id, e.g. P0.2-A>
disable-model-invocation: true
---

# Run DCLab prompt $ARGUMENTS

1. **Locate.** Find `$ARGUMENTS` in `docs/mvp/prompts/PHASE_*.md` or, for Track A
   prompts (`A2-UI`, `A3-UI`, …), `docs/mvp/prompts/ASSISTANT.md`. Read the prompt,
   its plan header (outcome, exit gate) in `docs/mvp/ROADMAP.md`, and
   `docs/mvp/EXECUTION_GUIDE.md`. Note the prompt's `Model:` line; it selects the
   implementer in step 5. You (the orchestrating session) can stay on a cheaper model.
2. **Check dependencies.** Confirm every prompt listed under "Depends on" is marked
   done in `docs/mvp/STATUS.md`. If not, stop and report which one is missing.
3. **Inspect before editing.** Record `git status`, current SHA, `.venv/bin/alembic heads`.
   Search for existing owners of the concern (services, tables, routes, tests).
   Use the Explore subagent for broad sweeps.
4. **Implementation packet (≤ 25 lines, in chat, not a file):** files to change,
   schema/API delta, invariants touched, tests to add, rollback. Stop and ask only if
   the prompt is ambiguous in a way that changes the schema or public API.
5. **Delegate implementation** to the subagent that matches the prompt's `Model:` line
   (each pins its own model and effort, so the switch is automatic). Pass it the full
   prompt text, your implementation packet and the files to read:

   | Prompt `Model:` line | Subagent |
   | --- | --- |
   | Sonnet 5.5 (any effort) | `implementer-sonnet` |
   | Opus 5.5 (high) or no effort given | `implementer-opus` |
   | Opus 5.5 (xhigh) | `implementer-opus-xhigh` |
   | Fable 5.1 | `implementer-fable` |

   Subagents cannot launch other subagents, so verification (step 6) and reviews
   (step 7) stay with you. Stay within the change budget (≈800 changed lines /
   20 files); split the prompt if it won't fit, never drop tests or security to fit.
   If an implementer agent is not available in this session, ask the user to start a
   new session, or fall back to the Agent tool's `model` parameter.
6. **Verify** with the commands in the prompt's "Verify" block via the `test-runner`
   subagent. Fix failures; re-run.
7. **Review** with the relevant subagents: `ml-correctness-reviewer` (engine/ML),
   `db-migration-reviewer` (db/alembic), `security-reviewer` (auth/API/MCP/agents).
   Address blocking findings.
8. **Record.** Mark the prompt done in `docs/mvp/STATUS.md` with date, SHA (after the
   user commits) and the exact verification commands/results. No other report files.
9. **Report** to the user: what changed, verification output, open risks, and the
   next eligible prompt ID with its recommended model. Do not commit or push unless asked.
