# Project Recap — What We Built, In Plain Language

This is a friendly walkthrough of everything done on this project, from the very first
step to right now. No jargon where it can be avoided — technical docs with the full
detail are linked at the bottom of each section.

---

## 1. What this project actually is

**Decision.ai / DCLab** is a small business app plus a research tool, both living in the
same codebase:

- **The product** (`apps/web` + `apps/api`): a web app where you upload sales
  "opportunities" (leads/deals), the system predicts which ones are likely to convert,
  and recommends what to do about each one ("call today," "send an email," "no
  action"). This is the customer-facing part.
- **The Lab** (`/lab` inside the same web app, backed by `app.engine.*` in the API): a
  more advanced screen where you can point the system at any dataset, define a
  prediction task, and it will automatically try many different models and feature
  combinations, then combine the best ones into an ensemble — like an automated data
  science assistant.
- **The Benchmark Harness** (`benchmarks/`, not a web page — a set of scripts): an
  honesty check. It answers one question: "is that automated multi-model Lab actually
  better than just training one well-tuned model by hand?" — and reports the real
  answer, including when the answer is "no."

---

## 2. Phase 1 — Making the app look and feel real

The app already had working features (opportunities, decisions, the Lab) but needed a
proper look and real marketing content instead of placeholder text. What changed:

- Re-themed the whole app to a clean, light, professional look (white/light-blue
  backgrounds, dark navy text and accents) instead of a generic starter theme.
- Rewrote the homepage and every marketing page (Platform, Solutions, Pricing,
  Company, etc.) so the words on the page describe what this product **actually
  does** — no invented features, no generic AI buzzwords.
- Cleaned up the header, footer, and branding so everything says "Decision.ai"
  consistently, and "Sign In" / navigation actually goes to the real product pages
  (dashboards, opportunities, decisions, Lab), not dead links.
- Rebuilt the dashboard page to show real numbers pulled from the real database, not
  placeholder charts.
- Added a `Workspace` concept in the database (a "container" that opportunities,
  predictions, and decisions all belong to), so the product is ready to support more
  than one customer/team later without a rebuild.

**Nothing about how the product works changed in this phase** — only how it looks and
what it says about itself.

---

## 3. Phase 2 — The Lab & Validation Environment

This phase made the "automated data science assistant" (the Lab) actually trustworthy,
by building in guardrails against the ways automated model search can quietly fool
itself:

- **Leakage detection** — checks that a model isn't accidentally "cheating" by seeing
  information from the future or information that's actually a disguised copy of the
  answer.
- **Proper train/validation/test splitting** — makes sure a model is judged on data it
  has genuinely never seen, split in a time-aware way so nothing from the future leaks
  into training.
- **Feature-group search** — instead of using one fixed set of input columns, the Lab
  tries meaningful combinations of feature groups (e.g. "customer info" + "purchase
  history" vs. "customer info" alone) to find what actually helps.
- **Diversity-aware ensembling** — when combining multiple models into one final
  answer, it deliberately avoids combining models that are all just copies of each
  other, and instead favors genuinely different models that complement one another.
- **Calibration checks against synthetic ground truth** — for made-up (synthetic)
  datasets where we control the real underlying answer, the Lab can check "does the
  model's 73% confidence actually mean 73% of the time it's right?" — a much stricter
  test than just accuracy.

Full technical detail: `docs/architecture.md`, `docs/model-search.md`,
`docs/leakage.md`, `docs/validation.md`, `docs/ensembles.md`.

---

## 4. Phase 3 — The Case Study Benchmark Harness (the honesty test)

This is the biggest and most rigorous phase. The question it exists to answer:

> **Does the Lab's "try many models and combine them" approach actually produce
> better business decisions than just training one single, well-tuned model —
> or are we adding complexity for nothing?**

The rule set from the start: the single model being compared against must be a *real,
seriously tuned* competitor — not a strawman set up to lose on purpose.

### The six test cases

Three ran on **real** e-commerce order data (Olist), three ran on **synthetic**
(made-up but carefully controlled) data where we know the true right answer:

| # | Question being predicted | Data |
|---|---|---|
| 1 | Will this customer order again in 60 days? | Real |
| 2 | Will this quiet customer come back? *(explicitly labeled a "proxy," not real churn)* | Real |
| 3 | How much will this customer spend in 90 days? | Real |
| 4 | Will this sales lead convert? | Synthetic, true answer known |
| 5 | Will this customer accept an upsell offer? | Synthetic, true answer known |
| 6 | Will this customer respond to a campaign? | Synthetic, true answer known |

### The steps we ran for every one of the six

1. **Set up the test case** — clearly define what's being predicted and how it will be
   judged.
2. **Train one honest, well-tuned baseline model** (a real 40-round tuning search, not
   a lazy default).
3. **Run the same case through the full Lab pipeline** (the automated multi-model
   search), using the exact same data split so it's a fair fight.
4. **Compare the actual business decisions** each one would produce — not just an
   abstract accuracy score, but real dollar impact, and who was right when they
   disagreed.
5. **For the synthetic cases**, compare both models against the true known answer
   directly (not just the noisy observed outcome).
6. **Check for cherry-picking** — break results down by customer segment and re-test
   across several different time windows, to see if the reported edge is a genuine,
   stable effect or "we got lucky with which slice of data we measured."
7. **Build one master scorecard** combining all of the above with a fixed rule for
   declaring a winner (agreed in advance, not adjusted afterward).
8. **Write the honest conclusion.**

### What the numbers actually showed

Out of 6 test cases:

- **0** cases where the Lab's multi-model approach was a clear, stable win
- **4** cases where the result was a genuine toss-up ("no meaningful difference")
- **2** cases where the single tuned model was clearly better

**Why:** the Lab's automated search currently does not do its own fine-tuning of model
settings (it tries many feature/model combinations, but not the deep tuning a human
data scientist would do) — while the baseline it was tested against got a real, proper
tuning pass. That's the single biggest reason. On top of that, two of the real-data
cases involved a very rare event (under 1% of customers), which makes any single
comparison noisy no matter which model you use.

One especially useful finding: on the "customer spending" test case, the Lab's combined
model had a *better average accuracy score* than the single model — but when we looked
at its actual predictions, they were all squeezed into a narrow range and never once
crossed the threshold needed to flag a customer as "high value." Good score, but
useless for the actual decision. That's exactly the kind of problem this whole exercise
exists to catch.

**Full detail, numbers, and the case-by-case reasoning:**
`docs/case-study-findings.md` (the plain-language write-up) and
`reports/case_study_scorecard.md` (the full scorecard table).

---

## 5. Phase 4 — Today: making sure everything actually works

We did a full health check of the whole system:

- Ran the app's entire automated test suite — **64 tests, all passing**.
- Rebuilt the web app for production — compiled cleanly, no errors.
- Checked code style/quality (linting) on the web app — clean.
- Started the real backend and hit every page's real API calls with real data —
  everything returned correctly.
- **Traced every single button/page in the web app to the exact backend endpoint it
  calls, and confirmed each one is wired correctly** (see below) — found zero broken
  connections.
- Re-ran the benchmark harness's key checks against the saved results — numbers still
  match, confirming the earlier findings are reproducible, not a fluke.

---

## 6. Where everything lives (if you want to look yourself)

| What | Where |
|---|---|
| Web app (what you see in the browser) | `apps/web/` |
| Backend API | `apps/api/` |
| The "Lab" automated model search engine | `apps/api/app/engine/` |
| Benchmark harness scripts | `benchmarks/` |
| Benchmark case study settings | `configs/case_studies/`, `configs/policies/case_studies/` |
| Benchmark results (raw numbers) | `artifacts/case_studies/` |
| Benchmark scorecard (summary table) | `reports/case_study_scorecard.md` |
| Benchmark honest write-up | `docs/case-study-findings.md` |
| This recap | `docs/PROJECT_RECAP.md` |
| How to actually use the app | `docs/HOW_TO_USE_THE_PLATFORM.md` |
