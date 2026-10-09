# Brief — DCLab demo v7 "Final product"

**Goal:** show the whole final product — every capability the plan (`docs/mvp/`) really contains — in
v6's plain, understandable style. Not over-simplified: real detail is kept (folds, metrics, thresholds,
checks, versions, roles, costs), but every word is one a data scientist already knows, and every
internal code is replaced by a name.

**Founder's rule:** honest and real. That means three things on screen:
1. Nothing invented beyond the plan. Every screen and button maps to a planned capability.
2. Every screen carries a small status tag: **Working today**, **Partly working** (what is missing),
   **Built, switched off** (AI pieces built but not shown to users until they pass testing), or
   **Planned · Phase N**. One page, "What's real today", lists them all.
3. Results are realistic, not only wins: an improve run that finds nothing better than noise, a drift
   alert with a 4-week label lag, a run that failed, a check that warns.

## خلاصه به فارسی

هدف: نمایش کامل محصول نهایی، یعنی همه‌ی قابلیت‌هایی که واقعاً در برنامه هست، با همان زبان ساده‌ی v6.
بیش از حد ساده نمی‌شود: جزئیات واقعی (foldها، متریک‌ها، threshold، بررسی‌ها، نسخه‌ها، نقش‌ها، هزینه‌ها)
می‌ماند، ولی هر کلمه آشنا برای یک دیتاساینتیست است و هیچ کد داخلی نمایش داده نمی‌شود. صادق و واقعی:
هیچ چیز خارج از برنامه اضافه نمی‌شود، هر صفحه برچسب وضعیت دارد (امروز کار می‌کند / تا حدی / ساخته شده
ولی خاموش / برنامه‌ریزی‌شده در فاز N)، و نتیجه‌ها واقع‌بینانه‌اند (مثلاً حلقه‌ی بهبودی که چیزی بهتر از نویز
پیدا نمی‌کند).

## Base

Start from `demo/product/v6_plain/` (copy its `app.css`, `_head.html`, `app.js`, `guide.js`,
`charts.py`, `build.py` and the six step pages as the starting fragments). Keep v6's look, words table,
guide strip, glossary, tooltips, flow bar and "Next step" buttons. Reuse v6's story and numbers exactly
(Northwind churn: 48,210 rows, 21.3% churn, PR-AUC ranking, recall ≥ 0.80, threshold 0.31 flags 27% in
CV, final test 1,731 / 406 / 1,017 / 5,750 = recall 0.81, precision 0.63 on 8,904 rows, weekly
scoring 6,412 flagged, `plan_type` drift PSI 0.22, precision 0.48 → retrained 0.59).

## Navigation (left sidebar, grouped)

- **Workspace:** Home · Inbox · Projects
- **Project · Northwind churn** (the six v6 steps, plus three): Data · Goal & test design ·
  Experiments · Improve · Model · Predictions · Monitoring · History
- **Assistant:** a right-hand chat panel opened from the top bar (not a separate step)
- **Admin:** Settings · AI settings · Connect (API, SDK, MCP)
- **Other views:** Business view (what a client sees) · Platform console (DCLab staff only)
- **What's real today**

A role switcher in the top bar (Data scientist · Admin · Business user) changes what the sidebar shows,
as the real product will (Business user sees only Business view).

## Screens

Each screen: title, one sentence, status tag, main content, details behind tabs, "Next step".

| Screen | Status tag | What it shows (plain words, real detail) |
| --- | --- | --- |
| Home | Working today | Greeting; "3 things need you" (from Inbox); active project as its steps with done/next; runs in progress; recent activity in sentences ("Run 4 finished · CatBoost · CV PR-AUC 0.70"); projects table (goal, best CV score, model in use, last run) |
| Inbox | Working today (AI items: Built, switched off) | Tabs: Needs your decision · Applied automatically (you can undo) · Done. Items: "Switch to the retrained model?" with evidence (precision 0.48 → 0.59 on the same test design), the automatic rule's answer, and Approve / Reject; "Leave `customer_id` out? (suggested by the assistant)" |
| Projects | Working today | List + "New project" wizard entry |
| Data | Working today | v6 Data, plus tabs: Versions (Jun 2026 file in use; Sep 2026 file uploaded by the monitoring retrain; one rejected upload with the reason "3 sheets could not be read"), Columns (type, missing %, used?, reason, and when the assistant suggested something: its suggestion next to the rule's answer), Data checks, Access (who can see it; "raw rows never leave the workspace") |
| Goal & test design | Working today | v6 page, plus "Who decided": target confirmed by you, test design suggested by the rules (time-ordered because churn rises over time) and confirmed by you |
| Experiments | Working today | v6 runs table (add Run 5 "failed: out of memory, retried" for realism) + **Run detail** tab: the steps of a run as a timeline (upload, profile, split, features, training 5 folds × 4 model families, choose best on CV, final test once, report) with time per step, and the 15 trust checks in plain words with pass/warn; Compare tab; "New run" and "Try a change" (branch: change class weights, add/remove a feature, change model families) |
| Improve | Planned · Phase 5 (engine being built) | Set a goal: "best precision while recall ≥ 0.80", budget (max runs 6, max time 2 h, max AI cost $2). Who proposes changes: rule table always; assistant suggestions optional. Iterations list: each a run with the change tried, why (which trust check or result motivated it), CV result ± spread. Honest outcome: 4 runs, best CV precision 0.65 vs current 0.63, spread ±0.02 → "No change beyond noise; current model kept". Rule shown: "Each try is compared on the same folds; the final test set is used once, only for an accepted result" |
| Model | Working today (releases: Planned · Phase 7) | v6 Model (threshold chooser, final test, model card) + tabs: Versions (model v1 in use; retrained v2 waiting for approval), Releases (Planned: approve a version for scheduled scoring; roll back to the previous one in one click), Saved file & checks (safe file format, the columns new data must have) |
| Predictions | Partly working (per-row reasons, schedule: Planned · Phase 7) | Score a file (working today); weekly schedule; this week's list with top 3 reasons per customer; download |
| Monitoring | Planned · Phase 7 | v6 Monitoring (weekly drift, real precision when outcomes arrive ~4 weeks late, retrained model side by side, Switch / Keep) + "What the system does on its own": detect drift (rule) → explain it (assistant, plain summary) → retrain on new data (automatic, can undo) → ask you before switching. Thresholds editable |
| History | Working today | Plain log of every decision in the project: who (you, a rule, the assistant), what, evidence, and Undo/Correct where allowed ("Corrections add a new entry; nothing is deleted"); tab Lineage: a simple diagram of file → test design → runs → model → predictions, with "built on old data" warnings when a newer file exists |
| Assistant panel | Built, switched off (shown to users after testing) | Chat: "Predict who will churn next month; catch 80%". It replies with what it did and plans; anything that changes the project appears as a suggestion card needing your OK; shows the steps it ran and the cost of the conversation ($0.19). Note: "Works with AI off: every action is also a button" |
| Settings | Partly working | Members & roles (admin, data scientist, analyst, business user) with what each can and cannot do; Usage & cost (compute minutes, AI cost vs budget: Planned · Phase 8); Integrations (Planned); Notifications (email for decisions and drift: Planned · Phase 8) |
| AI settings | Built, switched off (screen: Planned) | Plain version of governance: AI on/off for the workspace; what the assistant may do on its own vs must ask first, per kind of decision (e.g. "Leave out ID-like columns: automatic, you can undo"; "Change the test design: always ask"); monthly AI budget $25 with spend $9.40; which AI model is used; "Raw rows are never sent to an AI model; only column names and summary statistics"; incident log (empty). Each "automatic" setting shows how it was earned: "the assistant matched the rule's answer in 97% of 500 tested cases" |
| Connect | Working today | Use DCLab from code: Python SDK snippet, CLI, MCP for Claude Code / Cursor; access tokens with scopes (read, run, propose decisions) and revoke |
| Business view | Planned · Phase 7 | v6 client story: plain outcomes ("8 of 10 real churners caught"), this week's list download, "how the model was built" in three sentences, trust and limits, questions from the data team, and the boundary note ("DCLab does not contact customers or take business actions") |
| Platform console | Planned · Phase 8 (DCLab staff only) | Workspaces, job queue and worker health, quotas, backups; "staff never see customer rows" |
| What's real today | — | Table of every screen with its status tag and one line of what is missing; summary sentence: "The core flow — data, goal, experiments, trust checks, model, scoring a file — works today. Improve, monitoring, releases, the business view and the assistant come next." |

## Words

Use v6's "never / instead" table. Additional mappings for v7:

| Never | Instead |
| --- | --- |
| L1 / L2 / L3, trust level, autonomy | "Ask first" / "Automatic, you can undo" / "Automatic" |
| decision point | "kind of decision" |
| proposer, cross-check | "suggested by the rules" / "suggested by the assistant"; "both agreed" / "they disagreed: we asked you" |
| R3 evaluation | "tested on 500 past cases" |
| gateway, harness, governance, kill switch, incident | "AI settings", "AI on/off", "problem report" |
| agent, lead agent, Ops agent, Critic, Investigator, Jev | "the assistant" (one name for all AI help) |
| decision record, ref, stale, digest, lineage node | "History entry", "in use", "built on old data", (no digests), "diagram" |
| release, champion | "approved for scoring", "model in use" |
| service token, scopes | "access token", "what it can do" |
| tenant, workspace isolation | "your workspace's data stays in your workspace" |

## Status tag style

A small pill under the page title: green "Working today", amber "Partly working · <what's missing>",
blue "Built, switched off", grey "Planned · Phase N". Clicking it opens "What's real today".
On planned screens, a thin banner: "Preview of a planned screen. Numbers are examples."

## Build notes

- Self-contained folder `demo/product/v7_final/` (own `_head.html`, `app.css`, `app.js`, `guide.js`,
  `charts.py`, `build.py`, `src/*.html`, built pages). Do not edit anything outside it; do not load
  `../versions.js`.
- No banned words in visible text, tooltips or chart labels (grep the built pages; "holdout" never
  appears — "final test set (used once)").
- Numbers consistent across all pages with the v6 story and the Improve outcome above.
- Works at 1280px, 800px and 375px without horizontal scroll; inline SVG only.
- Open: `python3 -m http.server 8787 --directory demo/product` → `http://localhost:8787/v7_final/`.
