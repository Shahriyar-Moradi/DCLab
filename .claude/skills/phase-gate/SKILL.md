---
name: phase-gate
description: Evaluate whether a DCLab MVP phase exit gate is met — check every plan's prompts are done in docs/mvp/STATUS.md, run the gate's verification commands, and list missing evidence. Use when the user asks "is phase N done?" or before starting the next phase.
argument-hint: <phase number, e.g. 1>
disable-model-invocation: true
---

1. Read the Phase $ARGUMENTS section and its exit gate in `docs/mvp/ROADMAP.md`.
2. For every plan/prompt in the phase, check `docs/mvp/STATUS.md`; list any not done.
3. Run the gate's verification commands (delegate to `test-runner`), plus
   `gh run list --limit 3` to confirm CI is green on the current SHA.
4. For each gate criterion output: criterion → evidence (command/result or file) → PASS/FAIL/UNVERIFIED.
5. Verdict: phase complete only if every criterion is PASS. Otherwise list the
   minimal remaining prompts in order with their recommended models.
