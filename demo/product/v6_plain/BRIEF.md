# Brief — DCLab demo v6 "Plain"

**Goal:** a click-through demo that a data scientist understands in five minutes, with no glossary.
**Why:** the founder (a data scientist / ML engineer) could not follow about 70% of v5. The ML story in
v5 is simple; the confusion comes from internal codes (`DS v1`, `SP-1`, `MV v4`, `W-9`), AI-governance
words (L1/L2/L3, Jev, Critic, verifier, proposer, decision point) and platform words (ref, stale,
digest, decision record, feature contract) on every screen.

**Rule of thumb:** if a word would not appear in a normal data-science notebook or a scikit-learn tutorial,
it does not appear on the screen.

## خلاصه به فارسی

هدف: یک دموی کلیک‌پذیر که یک دیتاساینتیست در پنج دقیقه و بدون واژه‌نامه بفهمد. داستان همان داستان v5 است
(پیش‌بینی ریزش مشتری Northwind)، ولی در ۶ صفحه: داده ← هدف ← آزمایش‌ها ← مدل ← پیش‌بینی‌ها ← پایش.
اسم‌های واقعی به‌جای کدها، اصطلاحات رایج علم داده، و هوش مصنوعی فقط به‌صورت یک پنل اختیاری «پیشنهادها»
که پیش‌فرض خاموش است. ظاهر همان ظاهر v5 (Flow) است؛ فقط محتوا ساده می‌شود.

## The story (same data as v5)

Northwind Telecom wants to know which customers will cancel in the next 30 days, and must catch at
least 80% of them.

1. Upload `customers_2026Q3.csv`: 48,210 rows (customer-months), 31 columns, Jan 2025 – Jun 2026,
   9,412 customers, 21.3% churned.
2. Goal: predict `churned_next_30d` (yes/no). Rank models by PR-AUC. Business rule: recall ≥ 0.80.
   Test design: train on Jan 2025 – Mar 2026 with 5-fold time-ordered cross-validation; keep the last
   3 months (8,904 rows) as the final test set, used once at the end.
3. Experiments: a dummy baseline, then LightGBM, then LightGBM with balanced class weights, then
   CatBoost. The best is chosen on CV only.
4. Model: LightGBM with class weights, threshold 0.31, which flags about 21% of customers. Final test
   (used once): recall 0.81, precision 0.63, PR-AUC 0.71.
5. Predictions: every Monday the model scores the new customer file: 6,412 customers flagged (12.5%),
   each with its top 3 reasons, downloadable.
6. Monitoring: after a March marketing campaign a new plan type (`family_5g`) appears; `plan_type`
   drift PSI 0.22 (alert ≥ 0.20). When real outcomes arrive, precision has fallen to 0.48. A model
   retrained on data up to Sep 2026 gets 0.59 on the same kind of test. You approve the switch.

## Screens (six, plus a start page)

Every screen has: a title, one sentence saying what it is for, the main content, and a **Next step**
button that goes to the next screen. Keep v5's three-part guide strip ("What this is · What to do ·
What you get"), collapsible, in plain words. The flow bar at the top shows the six steps by name with
the current one highlighted.

| # | File | Title | Shows |
| --- | --- | --- | --- |
| 0 | `index.html` | Start | One project card (Northwind churn) showing the six steps and which are done; buttons Open project / Upload data |
| 1 | `data.html` | Data | File summary (rows, columns, date range, customers, churn rate); column table: name, type, missing %, **Used?** yes/no with a plain reason ("ID column, not a predictor", "Filled in only after the customer cancelled, so it would leak the answer", "Same value in every row"); class-balance-over-time chart; a short "Data checks" list (duplicates, missing values, leakage) |
| 2 | `goal.html` | Goal & test design | What we predict, task type, ranking metric (PR-AUC, with one sentence why), business rule (recall ≥ 0.80); how we test: a time-line graphic with the training period (5 time-ordered CV folds) and the final test period (last 3 months, used once); one sentence on why time-ordered (the churn rate rises over time) |
| 3 | `experiments.html` | Experiments | Runs table named in words ("Run 1 · Baseline (always predicts 'no')", "Run 2 · LightGBM", "Run 3 · LightGBM + balanced class weights", "Run 4 · CatBoost"): model, what changed vs the previous run, CV PR-AUC ± std, beats baseline?, trust checks (✓ count, ⚠ count); the chosen run is marked "Best on cross-validation"; a "Trust checks" panel listing the checks in plain words (leakage, overfitting gap, duplicates, class imbalance, too-good-to-be-true, calibration, fold stability, drift between train and test, …) with pass/warn; a simple compare view of two runs |
| 4 | `model.html` | Model | The chosen model in one sentence; threshold chooser: chart of precision and recall by threshold from the CV folds, the recall-0.80 line, the chosen point, and the sentence "At threshold 0.31 the model flags 21% of customers and catches 81% of churners"; final test result (used once) with recall, precision, PR-AUC and a confusion matrix in counts; model card: what data, which features matter most (bar chart of top 8), known limits; button **Use this model for predictions** |
| 5 | `predictions.html` | Predictions | Score a new file (upload box); this week's list: customer id, churn probability, flagged?, top 3 reasons in words ("monthly fee rose 20%", "no login for 45 days", "contract ends this month"); download CSV; last scored Monday 02:00 |
| 6 | `monitoring.html` | Monitoring | Weekly chart of input drift (PSI per feature, alert line 0.20) and real precision once outcomes arrive (outcomes arrive about 4 weeks late); what changed in plain words; the retrained model side by side with the current one (precision 0.48 → 0.59, same test design); buttons **Switch to the retrained model** / **Keep the current model** |

## Words — use these, never those

| Never on screen | Use instead |
| --- | --- |
| `DS v1`, `DS v3` | "Customer data · Jun 2026", "Customer data · Sep 2026" |
| `SP-1`, split plan | "Test design" |
| `FR v3`, feature recipe | "Features (29 used, 2 left out)" |
| `E1`…`E9`, experiment ids | "Run 1", "Run 2", … with a short name |
| `MV v4`, model version, champion ref ★ | "Churn model (in use)", "Churn model · retrained" |
| `R-15`, release | "Switch to this model" |
| `W-9` | "Week of 1 Sep" |
| `IL-2`, improve loop, proposer | not in v6 (see "Later") |
| L1 / L2 / L3, trust level, decision point | "Suggestion" (needs your OK) |
| Jev, Critic, verifier, NOOA, agent run, harness, gateway | "AI suggestion" or "automatic check" |
| decision record `d-402`, digest `7f3a…`, ref, stale, ref move | not shown; at most "History" with plain sentences ("You switched to the retrained model · Mon") |
| feature contract, package & integrity, quarantined pickle | "New files must have the same columns" (only where needed) |
| holdout | "final test set (used once)" |
| governance, policy, budgets, kill switch, R3, platform caps | not in v6 |

Standard terms that stay (each with a one-line tooltip the first time): cross-validation, PR-AUC,
recall, precision, threshold, class weights, baseline, leakage, drift, PSI, LightGBM, CatBoost.

## AI in v6

A single switch in the top bar, **AI suggestions: off** by default. When on, a right-hand panel shows at
most three plain suggestions on the current screen, each with **Accept** / **Ignore** and a one-line
reason, e.g. on Data: "`customer_id` looks like an ID; leave it out" (Accept / Ignore); on Experiments:
"Try balanced class weights: churners are 21% of rows". Everything works with the switch off. No
confidence scores, no model names, no costs on these panels.

## Look

v5's look exactly: `app.css`, Manrope / Inter / JetBrains Mono, white surfaces, teal accents, the same
cards, tables, tabs, pills and charts (inline SVG). Fewer elements per screen: at most two primary
buttons; details behind tabs where a screen gets long. Large type, white space. Works at phone width.

## Navigation

Left sidebar: the six steps (Data, Goal & test design, Experiments, Model, Predictions, Monitoring)
plus Start. No Inbox, Lab, Graph, Pipeline, Decisions, Agents, Governance, Settings or Operator screens.
A small "About this demo" note: all names and numbers are invented; nothing calls a service.

## Later (not in v6)

Improve loop ("best precision with recall ≥ 0.80, automatically"), chat assistant, inbox, audit
history, roles, client view, operator console. Each comes back only when it can be explained in one
plain sentence.

## Build notes

- Self-contained folder `demo/product/v6_plain/`: own `_head.html`, `app.css` (copied from v5),
  `app.js` (nav for these seven pages only), `build.py` (same fragment → page pattern as v5),
  `src/*.html` fragments and the built pages.
- Do not edit `versions.json`, `index.html`, `versions.js` or `versions.py` in `demo/product/` (another
  session edits them); do not load `../versions.js`. Register the version later with
  `python3 demo/product/versions.py` once that session is idle.
- Open with `python3 -m http.server 8787 --directory demo/product` → `http://localhost:8787/v6_plain/`.
