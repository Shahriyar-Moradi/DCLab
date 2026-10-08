# DCLab product UI preview

A static, click-through prototype of the planned DCLab product. **Every version
is kept**: each lives in its own folder and is never edited once a newer one
exists.

| Version | Name | What it is |
| --- | --- | --- |
| `v5_based_plans/` | Based on the plans (latest) | v5's look, mapped to `docs/mvp`: every screen shows its Studio route, phase, prompt and status; the flow bar uses the state-graph node names; "Built today" view and "AI off" switch; roles and landing routes per STUDIO_DESIGN §3; a Plan page (`map.html`) built from ROADMAP, ARCHITECTURE, AGENTS_NOOA_JEV, STUDIO_DESIGN and STATUS |
| `v5/` | Flow | The state graph is the navigation: a flow bar on every project screen, Home shows the active project as its eight steps, cleaner surfaces, plain tags, a lighter three-part guide. Every v2 detail kept |
| `v4/` | Guided | Version 2's full detail, reorganized: workflow sidebar, step bar, a guide on every screen, explained terms, glossary, next step |
| `v3/` | Clean | The product look: 10 screens, one job each, details behind tabs, plain words |
| `v2/` | Detailed, tabbed | Every detail kept; 16 screens, dense cards split into section tabs |
| `v1/` | Detailed | The first full walkthrough; 16 screens with every field and review note |

`versions.json` is the list. `index.html` (generated) is the landing page, and
`versions.js` (generated) puts a version button in the bottom-right corner of
every screen; switching keeps you on the same screen when the other version has it.

## Open it

```bash
python3 -m http.server 8787 --directory demo/product
```

Then visit `http://localhost:8787/`. No dependencies or services are needed.

## Make a new version

```bash
python3 demo/product/versions.py new "Name" "One-line summary"
```

This freezes the latest version and creates the next folder as a copy of it.
Add `--from vN` to start from an older version instead (v4 started from v2; v5 from v4;
`v5_based_plans` from v5). Add `--id vName` to name the folder instead of taking the next
number (letters, digits, `_` and `-` after the `v`).
Edit page content in `vN/src/*.html` (and `vN/app.css`, `vN/app.js`,
`vN/index.html`), then rebuild everything:

```bash
python3 demo/product/versions.py build
```

Do not edit older folders; that is what makes them versions.

## Design rules for v3

1. One job per screen: a title, one sentence, at most two buttons.
2. Tabs across the top. The first tab is the answer; details are in the others.
3. Plain words: "Observe", "Ask first", "Automatic"; no record ids, digests or
   prompt versions on main views.
4. Evidence is one click away, not on the surface.
5. A few roomy blocks per tab.

## Boundaries

All names, numbers, events and spend are invented. Buttons, tabs, search and
switches respond in the browser; nothing calls DCLab, a model or any service.
The screens show a target experience, not implementation status: see
`docs/mvp/` for that.

## What v4 adds on top of v2

- Sidebar ordered by the workflow: Build a model (1 Data → 5 Improve), Use and
  watch (6 Models, 7 Monitoring), Trace and control.
- A step bar on project screens showing where you are and what is done.
- A guide under every title: what the screen is for, how to use it in three
  steps, what you get, and what needs attention now. "Hide guide" remembers
  the choice.
- ML and AI terms are underlined once per tab and explained on hover or focus;
  the Glossary button lists all of them.
- A "Next step" card at the end of every screen.

The guide texts, steps and glossary live in `v4/guide.js`.

## What v5 changes on top of v4 (the "Flow" direction)

Chosen on 2026-10-07 from four sampled directions (kept in `_options/` for
reference: A Editorial, B Workbench, C Flow, D Console).

- **Flow bar.** Every project screen starts with the project's state graph:
  Data → Goal → Split · features → Experiments → Improve → Champion → Release →
  Monitoring. Green is done, violet is running, amber needs you, the dark node
  is where you are. Each node opens the screen that owns it (`v5/guide.js`).
- **Home** shows the active project as the same eight steps, then all projects,
  decisions waiting and the last 24 hours.
- **Sidebar**: Workspace · Projects (with status dots) · the open project's
  screens · Preview. The assistant is one click away from the top bar.
- **Design system** (`v5/app.css`): cool white `#F4F6FA`, near-black primary
  buttons, teal for links and the deterministic side, violet for anything AI,
  amber for "needs you"; Manrope headlines, Inter body, JetBrains Mono ids;
  16 px cards with a soft shadow; sentence-case status tags with a dot;
  segmented section tabs. Dark theme via `prefers-color-scheme`.
- **Guide** is one compact strip under the title: what this screen is · how to
  use it · next step. "Hide" is remembered. Glossary and explained terms as in v4.
- Fonts load from Google Fonts with system fallbacks; nothing else is fetched.

## What `v5_based_plans` adds on top of v5 (2026-10-08)

Same design; the content and structure follow `docs/mvp` instead of the older
prototype notes.

- **Plan page** (`src/map.html`): product definition, three views of one truth,
  order of work with status, phases table, Phase 4 stages, exit gate, the hybrid
  AI model (rules, trust levels L0–L3, AI-before / AI-after / cross-check,
  agents and Jev purposes), the state graph node table, scientific invariants,
  screen → route → prompt → status map, the sync rule.
- **Every screen carries its plan meta** (`guide.js` `PAGE_META`): a route chip
  in the top bar and a line in the guide (route · phase · prompts · built /
  in progress / planned), from STUDIO_DESIGN §4 and STATUS.md.
- **Flow bar nodes are the state-graph nodes**: DatasetVersion, ProblemSpec,
  SplitPlan, FeatureRecipe, Experiment, Improve loop (Phase 5), ModelVersion,
  Release (Phase 7), MonitoringWindow (Phase 7).
- **Built today / Full MVP plan** selector in the top bar. In "Built today",
  screens whose backend is not merged get a banner, and tabs or panels tagged
  `data-arrives="Phase 7 · P7.2-A"` are dimmed (the sync rule: no backend, no
  element). Tag any element with `data-arrives` to include it.
- **AI on / off** switch: dims AI notes, proposals, agent messages and levels,
  and shows the deterministic path (hybrid AI model, rule 9).
- **Sidebars per role** exactly as STUDIO_DESIGN §3: Workspace (Home, Inbox,
  Governance, Agents & tools, Settings) · Projects · Project (Lab, Pipeline,
  Graph, Data, Experiments, Improve, Models, Monitoring, Decisions); client
  → `/outcomes`; operator → `/operator`.
- **Role chooser** states the product in one line and each role's landing route.
