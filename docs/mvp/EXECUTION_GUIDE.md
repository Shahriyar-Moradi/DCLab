# Execution guide — running prompts with Claude Code

## 1. Loop for each prompt

```
/run-prompt P1.4-A        # implements one prompt end to end (skill)
/verify                   # re-run checks for the diff
review → commit → PR      # you commit; one prompt = one PR
/phase-gate 1             # at the end of a phase
```

The `run-prompt` skill does: dependency check → inspect → short implementation
packet (in chat) → implement → verify (`test-runner` subagent) → reviews
(`ml-correctness-reviewer`, `db-migration-reviewer`, `security-reviewer`) →
STATUS.md line → report with the next prompt.

## 2. Model selection rubric

Prices per 1M tokens (input/output): Fable 5.1 $10/$50 · Opus 5.5 $4/$20 ·
Sonnet 5.5 $2/$10 · Haiku 4.5 $1/$5. Choose by **risk × ambiguity**, not size.

| Model | Use for | Effort |
| --- | --- | --- |
| **Fable 5.1** | ADRs that fix schema/security/authority for many later prompts; cross-cutting designs (state graph, improve loop, agent runtime); phase-gate reviews; anything where a wrong decision is expensive to undo | high |
| **Opus 5.5** | Default for implementation: migrations, services, ML engine, MCP server, agent runtime, refactors with blast radius | high (xhigh for engine/security) |
| **Sonnet 5.5** | Bounded, well-specified work: UI pages/components, CLI, docs, CI/dependency fixes, test additions, mechanical moves | medium |
| **Haiku 4.5** | Subagent search/log triage only (Explore-style), never for code that ships | low |

**The switch is automatic.** Keep your main session on **Sonnet 5.5 (high)** for
orchestration. `/run-prompt` delegates the implementation to a subagent that pins its
own model and effort (`.claude/agents/`): `implementer-sonnet` (Sonnet 5.5, medium),
`implementer-opus` (Opus 5.5, high), `implementer-opus-xhigh` (Opus 5.5, xhigh),
`implementer-fable` (Fable 5.1, high). The prompt's `Model:` line picks which one.
Reviewers and the test runner also pin theirs. A session cannot change its own model;
only you can, from the model menu, so use delegation instead. Pins use full model IDs
(`claude-opus-5-5`, etc.) because alias resolution is undocumented.

## 3. Prompt format (in `prompts/PHASE_N.md`)

```
### P1.4-A — Title
Model: Opus 5.5 (high) · Size: M · Depends on: P1.1-A · Review: ml-correctness
Goal: one sentence outcome.
Read: exact files.
Do: bullets.
Don't: bullets (scope fences).
Verify: commands.
Done when: observable criteria.
```

## 4. Definition of done (every prompt)

1. Named verification commands run and pass (output summarized in STATUS.md).
2. Relevant reviewer subagents report no blocking findings.
3. No new parallel owner for an existing concern; no TODO left for the core outcome.
4. Migrations: single head, upgrade/downgrade tested, truth artifacts regenerated.
5. Behavior that is disabled/not yet supported is explicit, not faked.
6. STATUS.md updated; nothing else in docs unless the prompt says so.

Exact-SHA CI is required at **phase gates**, not for every prompt.
