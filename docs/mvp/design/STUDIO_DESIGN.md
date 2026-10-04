# Studio design — target UI, synced to the backend

Adopted 2026-10-04 (founder decision). The click-through prototype in
[`prototype/`](prototype/) (open `prototype/index.html` in a browser; all data is
invented) is the **visual and structural target** for Studio. This document
turns it into buildable rules: the design system, the shell, and for every
screen the route, the backend it reads, and the prompt that builds it.

## 1. The sync rule (non-negotiable for every UI prompt)

1. **Every value on screen comes from an API field.** Screens are typed against
   the generated `/v1` types (P4.0-A). No number, list or status is copied from
   the prototype's sample data.
2. **No backend, no element.** A prototype element whose backend does not exist
   yet is not drawn (or is shown as an explicit empty state naming the phase:
   "Monitoring arrives with releases"). It is never faked.
3. **Prototype ≠ status.** A screen existing in the prototype says nothing about
   what is implemented; [UI_COVERAGE.md](../UI_COVERAGE.md) and STATUS.md do.
4. **Rule answer beside AI answer.** Wherever an AI (agent, Jev, assistant)
   contributes, the deterministic answer is shown next to it, as the prototype does.
5. **Works with AI off.** Each AI element has the non-AI path the prototype labels
   "Use forms instead" / "With AI off".

## 2. Design system (from `prototype/app.css`)

Port into `apps/web` as Tailwind theme tokens + CSS variables (P4.0-B). Keep the
names so prototype markup and Studio components stay easy to compare.

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--bg` | `#F1F6F6` | `#0F1514` | page |
| `--surface` / `-2` / `-3` | `#FFFFFF` / `#F3F8F8` / `#E7F0F0` | `#171E1D` / `#1E2726` / `#26312F` | cards, hover, chips |
| `--ink` / `--muted` | `#182320` / `#5E6E6A` | `#E4EAE8` / `#9AA8A4` | text |
| `--line` / `--line-strong` | `#DEE8E7` / `#C6D5D3` | `#2B3634` / `#3A4745` | borders |
| `--accent` | `#1E8FB6` | `#6FB8D8` | links, primary buttons |
| `--ai` | `#2A7A96` | `#7CC0D6` | anything AI-produced |
| `--ok` / `--warn` / `--crit` | `#1F8A42` / `#A86F12` / `#BE3E36` | `#5FC48A` / `#E3AA4E` / `#EE7D75` | status |

- Type: system UI stack, 16 px body, h1 1.9 rem/700, h2 1.3 rem, h3 1.08 rem;
  monospace for ids, digests, code. Radius 14 px (cards) / 9 px (controls).
- Theme: light default, dark via `prefers-color-scheme` and `data-theme`.
- Components (one React component each, in `components/studio/`):
  `Shell` (sidebar 264 px + sticky glass top bar), `Crumbs`, `CommandBar` (⌘K),
  `PageHead` (h1 + sub + toolbar), `Card`, `SectionTabs` (the `.sect` card whose
  `<section data-tab>` children become tabs), `Tabs`, `Pill` (`ok|warn|crit|ai|det|gray`),
  `Level` (`L0–L3` badge), `Stat`, `DataTable`, `KeyValue`, `EventList`
  (`.events .ev det|ai`), `Chat` (`.msg user|agent`, composer), `Banner` (`ai`
  variant), `ReviewNotes` (dev-only, off in production), `CodeBlock`, `Chart`
  (recharts, styled like the prototype's SVG charts).

## 3. Shell and information architecture (from `prototype/app.js`)

Three workspaces chosen by **role after login** (the prototype's chooser page
`index.html` is a demo device, not a product screen):

| Role | Lands on | Sidebar |
| --- | --- | --- |
| developer / admin (Developer Studio) | `/home` | **Workspace:** Home · Inbox · Governance · Agents & tools · Settings — **Project ‹name›:** Lab (chat) · Pipeline · Graph · Data · Experiments · Improve · Models · Monitoring · Decisions |
| client (Business Outcomes) | `/outcomes` | Outcomes · Prediction list · Questions & reports |
| platform operator | `/operator` | Overview · Jobs & workers · Quarantine · Platform AI caps · Benchmarks & gates |

Top bar: breadcrumbs (workspace › project › page), ⌘K command bar (jump to
project / experiment / model / decision by name or id), AI state pill (assistant
on/off from settings), workspace switcher (existing `X-Workspace-Id` flow).
Sidebar items whose backend does not exist yet are hidden (rule 1.2); counts
(`Inbox 7`, `Experiments 9`) come from list endpoints.

## 4. Screen map — prototype → route → backend → prompt

Status of the backend today: **✓** exists in `/v1` · **P** planned (prompt) · **NEW** endpoint this plan adds.

| Prototype | Studio route | Backend it reads | Built by |
| --- | --- | --- | --- |
| `home.html` Home | `/home` | ✓ `GET /v1/projects`, refs, model-versions · NEW `GET /v1/activity` (decision records + run events, workspace-wide) · NEW `GET /v1/inbox?count` · spend card after A2-A (`llm_invocations`) / P8.4 (usage) | P4.15-A, P4.15-UI |
| `inbox.html` Inbox | `/inbox` | ✓ project decisions (`status=proposed`) · NEW `GET /v1/inbox` (proposed decisions + assistant/agent proposals + open questions, workspace-wide, tabs: needs a decision / applied automatically / done) | P4.16-A, P4.16-UI |
| `lab.html` Lab (chat) | `/projects/[id]/lab` | P `POST /v1/assistant/threads…` (SSE), proposals confirm · ✓ everything the tools read | A3-UI, A4-A |
| `pipeline.html` Pipeline | `/projects/[id]/pipeline/[experimentId]` | ✓ `GET /v1/model-builds/{id}`, `/events`, `/artifacts` · ✓ decisions of the run · P findings (P4.10) · AI notes per stage only after Phase 6 | P4.17-UI |
| `graph.html` Graph | `/projects/[id]/graph` | ✓ `GET /v1/projects/{id}/graph`, `GET /v1/nodes/{kind}/{id}/impact`, refs | P4.2-A |
| `data.html` Data | `/projects/[id]/data` | ✓ `GET /v1/datasets`, `GET /v1/datasets/{id}`, `POST /v1/datasets` · NEW `GET /v1/datasets/{id}/profile` (columns, types, roles, missing, unique, transforms; training rows only) · ✓ leakage audit via experiment evidence · P findings (P4.10) · "AI (Jev)" column only after P6.7 · policy tab reads ADR 0005 policy (NEW read field on dataset) | P4.1-C, P4.10-UI |
| `experiments.html` Experiments | `/projects/[id]/experiments[/[eid]]` | ✓ list/detail/compare/branch/cancel/code · P findings · Critic review only after P6.4 | P4.1-A, P4.3-A, P4.4-A |
| `improve.html` Improve | `/projects/[id]/improve` | P `/v1/improve-runs` (P5.4) | P5.5-A |
| `models.html` Models | `/projects/[id]/models[/[mvId]]` | ✓ `GET /v1/model-versions/{id}` · P predictions (P4.9), card (P4.11) · releases, contract, package (P7.1–P7.2) | P4.9-UI, P4.11-UI, P7.5-A |
| `monitoring.html` Monitoring | `/projects/[id]/monitoring` | P monitoring windows, label upload (P7.4) | P7.5-A |
| `decisions.html` Decisions | `/projects/[id]/decisions` | ✓ `GET/POST /v1/projects/{id}/decisions`, accept/reject/supersede ("Revert"/"Correct" = supersede) | P4.4-A |
| `agents.html` Agents & tools | `/agents` | tabs: Connect ✓ (static + token) · Tokens ✓ `/v1/service-tokens` · Tool registry P `contracts/agent_tools.json` (A2-B) · Agent runs P `agent_runs` (A2-A) · Agent catalog P (P6.8) | P4.8-UI, A2-B, P6.8-UI |
| `governance.html` Governance | `/governance` | P switches + budgets (A1/A2-A, P8.4) · ledger + replay (A2-A) · trust levels (ADR 0008 decision) · evaluation R3 (P6.8) | P6.9-A |
| `settings.html` Settings | `/settings` | ✓ members (existing admin APIs) · P usage (P8.4) · P integrations (MLflow P7.3, compute P8.3) · P data safety (P8.7) · P notifications (P8.8) | grows per phase |
| `client.html` Business Outcomes | `/outcomes` | P predictions (P4.9/P7.2), model card (P4.11) · per-row reasons (P7.7) · questions = proposals of kind question · plain-language Q&A = assistant with read-only client tools | P7.6-A |
| `operator.html` Operator console | `/operator` | P queue/worker health (P8.5), quarantine (P8.7), platform caps (ADR 0008/0009 config), benchmarks (R1/R2 results) | P8.5-UI |
| `map.html`, `index.html` | — | demo devices; not product screens | — |

## 5. Gaps the prototype exposed, and where they now live

| Gap | Decision |
| --- | --- |
| Inbox and activity need workspace-level read models | NEW endpoints in P4.15-A / P4.16-A (read-only projections over decision records, proposals and run events; no new tables) |
| Data page needs column-level profile | NEW `GET /v1/datasets/{id}/profile` in P4.1-C over the existing profile/columns tables |
| Trust levels L0–L3 (auto-apply with revert) | **Not in the MVP authority model yet.** MVP = L1 (propose → human confirms) and L0 (off). L2/L3 need ADR 0008 to decide; the UI shows the `Level` badge only for levels the backend reports |
| Per-row prediction reasons ("why is C-48112 flagged?") | P7.7-A: deterministic explainer in the engine (worker), ml-correctness review |
| Label arrival for monitoring | added to P7.4-A (label upload as a dataset version with purpose `labels`) |
| Notifications and webhooks (inbox items, run done, drift) | P8.8-A |
| Report agent text | deterministic templates first (model card P4.11, outcome summary P7.6); AI text only if P6.8 allows |
| Analyst role (run, not accept) | open question for the capability matrix; not planned |
| Chat sessions persistence and "report when I leave" | A2-A threads persist; inbox item on run completion (P4.16), email/webhook in P8.8 |
| Cleaning actions (dedupe, missing indicators) | open: proposal kind vs cleaning recipe; decide in ADR 0008 |

## 6. How a UI prompt uses this

Each UI prompt names its prototype file (`Design: prototype/experiments.html`,
tabs …). The implementer: (1) reads the prototype screen, (2) lists every element
and the API field behind it, (3) drops or empties elements without a backend,
(4) builds with `components/studio/*`, (5) adds a Playwright spec against the
seeded real backend, (6) updates UI_COVERAGE.md.
