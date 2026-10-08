# V7 build plan — build the product the v7 demo shows

**Decided by the founder, 2026-10-08:** the click-through demo `demo/product/v7_final/` (plain words,
v6 direction, real detail, honest status per screen) is the target for the product. Its brief,
`demo/product/v7_final/BRIEF.md`, is the screen-by-screen spec; `demo/product/v6_plain/BRIEF.md` holds
the words table. This plan replaces the order of the remaining prompt queue; the prompts in
`docs/mvp/prompts/` stay as design reference for the backend pieces they describe.

**Rules**
- Every screen uses the v6/v7 words table: no internal codes (`DS v1`, `SP-1`, `MV v4`, short ids
  as names), no AI-governance words (L1/L2/L3, Jev, Critic, verifier, proposer, decision point), no
  platform words (ref, stale, digest, decision record, feature contract, champion). "Holdout" becomes
  "final test set (used once)". Ids stay available in a details tab or copy button, never as names.
- Honest by default: a capability that is not built is hidden or shown as "Planned" — never a fake.
- The AI-governance and agent build-out is frozen (no new work); the assistant stays switched off for
  users until it passes testing (checkpoint G6). Existing backend stays; nothing is deleted.
- Non-negotiables in `CLAUDE.md` still apply (tenancy, evidence immutability, final test used once,
  AI advisory, additive migrations). CI is skipped until all of this is done (founder, 2026-10-08).

## Stage A — make what works look and read like v7 (web only)

| Step | Scope | Status |
| --- | --- | --- |
| A1 | Navigation and language: v7 grouped sidebar (Workspace · Project steps · Admin), project flow bar, guide strip, term tooltips + glossary; plain-words pass on shared components; names instead of codes | next |
| A2 | Data and Goal & test design screens to v7 | — |
| A3 | Experiments (runs table in words, run timeline, 15 trust checks in plain words, compare, "Try a change") | — |
| A4 | Model (threshold chooser, final test once with confusion matrix, model card, versions) | — |
| A5 | Predictions (score a file) and History (plain decision log, lineage diagram) | — |
| A6 | Home and Inbox to v7; Connect page to v7 | — |

## Stage B — build what v7 marks as missing (in this order)

| Step | Capability | Backend reference |
| --- | --- | --- |
| B1 | Per-row reasons in scoring (top 3 reasons per customer) | P7.7-A |
| B2 | Approve a model for scheduled scoring + roll back; weekly schedule | P7.1-A, P7.2-A |
| B3 | Monitoring: weekly drift, real performance when outcomes arrive, retrain proposal | P7.4-A, P7.5-A |
| B4 | Improve: goal + budget, rule-table proposer only, same folds, final test once for an accepted result | P5.3-A, P5.4-A, P5.5-A (rule-only first) |
| B5 | Business view for business users | P7.6-A |
| B6 | Settings: members and roles in Studio; usage and cost | P4.14-A, P8.4-A |
| later | Assistant panel and AI settings shown to users (after G6); platform console; notifications; cloud | A3-UI, P6.11-UI, P8.x |

Each step: one reviewed change set, local verification (targeted tests, web checks, Playwright where a
flow changes), commit on `dev`, push.
