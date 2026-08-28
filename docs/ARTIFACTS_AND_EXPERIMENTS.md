# What `artifacts/` is — case studies vs experiment folders

`artifacts/` is **not the product**. It is a scratch disk for files the engines
write when they train or compare models. Git ignores almost all of it
(`.gitignore`: `artifacts/**`). You can delete it; the website will still
start. The next Lab run or benchmark will recreate what it needs.

There are two completely different piles inside it:

```
artifacts/
├── case_studies/     research benchmark (6 business questions)
└── experiments/      one folder per Lab run (lots of UUID names)
```

---

## 1. `artifacts/case_studies/` — the six case studies

This is the **honesty check**: “is DCLab’s multi-model Lab actually better than
one carefully tuned model?”

It is **not** the customer app (Opportunities / Decisions). It is **not**
Client Labs. Code lives in `benchmarks/`. Human write-up:
`docs/case-study-findings.md`. Summary table:
`reports/case_study_scorecard.md`.

There are **six** case studies (six business questions):

| Folder | Plain meaning | Data |
|---|---|---|
| `purchase_prediction` | Will this shopper buy? | Real Olist e-commerce |
| `reactivation` | Will a quiet customer come back? (repeat-purchase **proxy**, not real churn) | Real Olist |
| `customer_value` | How valuable is this customer? | Real Olist |
| `lead_conversion` | Will this lead become a customer? | Synthetic (made-up, so we know the “true” answer) |
| `upsell_crosssell` | Will they take an add-on / extra product? | Synthetic |
| `campaign_response` | Will they react to a campaign? | Synthetic |

Each of those six folders then has **up to four subfolders**. Those are
**steps of the same comparison**, not four extra case studies.

### `baseline/`

Step 1: train **one** well-tuned sklearn model (e.g. 40-trial search).

Typical files:

- `result.json` — metrics, features used, split sizes, seed
- `val_predictions.csv` / `test_predictions.csv` — scores on holdout rows

### `decision_impact/` (sometimes missing a sibling if a step was skipped)

Step 3: turn both models’ scores into **business decisions** (act / don’t act)
and compare money/value, not only AUC.

Typical files:

- `result.json` — lift, agreement rate
- `per_entity_decisions.csv` — one row per customer/lead: what each side would do

### `calibration/`

Step 4: “when the model says 70%, is it right ~70% of the time?”

Only present when we have **known truth** (the three synthetic studies).
Olist studies often have no `calibration/` folder because real data has no
planted true probability.

Typical files:

- `result.json`
- `per_entity_calibration.csv`

### `segment_comparison/`

Step 5: does the “winner” stay the winner in **slices** (segments) and across
**time windows** (walk-forward folds)? A win that flips every month is not
treated as a real win.

Typical files:

- `result.json`

**Headline result of all six:** the multi-model Lab did **not** clearly beat
the single tuned baseline on business decisions. That is why the case-study
docs exist — to record an honest “no,” not to power the customer UI.

---

## 2. `artifacts/experiments/` — why so many UUID folders?

Each folder name like `01d199b9-dc4c-4be2-a9e3-…` is **one Lab experiment
run**: the engine tried several models, wrote caches and member files, and
left a `result.json` + `report.md`.

Inside one folder you typically see:

| Item | Meaning |
|---|---|
| `result.json` | Scores, selected models, metrics |
| `report.md` | Human-readable write-up of that run |
| `model.joblib` | The served / blended model for that run |
| `members/` | Individual models that were kept |
| `cache/` | Temporary fitted pieces so the run could skip repeat work |

**You did not have to click “Run experiment” 107 times.** The disk currently
has on the order of **100+** of these folders. They come from:

1. **Automated tests** (`pytest`, especially `tests/engine/`). Almost every
   test that calls `create_experiment` / `execute_experiment` writes a new
   UUID directory. Tests **wipe the test database** afterward, but they do
   **not** delete these files. So the folder count grows forever.
2. **Admin Lab / CLI runs** on the real database (`decisionai`). Those also
   create a folder **and** a row you can see under `/admin/lab/experiments`.
3. **Benchmark Step 2** (`benchmarks/dclab_runner.py`) runs the same Lab
   engine for each case study and leaves experiment folders behind.

That is why it feels like “we have no experiments” in the **customer** app:

- Customers have **Opportunities / Decisions / Client Labs**, not this list.
- Staff see experiments only at **`/admin/lab/experiments`**, and only rows
  still in Postgres.
- On disk you can have **more folders than database rows**. Example from a
  local machine: ~107 folders, ~40 rows in `decisionai`, **0** rows in
  `decisionai_test` (tests truncated the table, leftover files remain).

Orphan UUID folders are **junk from tests and old runs**, not a secret second
product.

---

## 3. What you can safely do

- **Do not** treat `artifacts/` as source code. Do not hand-edit UUID folders.
- **Safe to delete** `artifacts/experiments/*` if you want disk space. New
  tests and Lab runs will recreate what they need. Admin experiment pages
  that pointed at a deleted folder will miss files until you re-run.
- **Safe to keep** `artifacts/case_studies/` if you still want the scorecard
  numbers without re-running the whole benchmark (hours of training).
- Rebuilding the scorecard without retraining:  
  `python -m benchmarks.scorecard --out reports/case_study_scorecard.md`  
  (needs the JSON files under `case_studies/` to still exist).

---

## 4. One-line memory aid

| Path | In one sentence |
|---|---|
| `artifacts/case_studies/<name>/baseline` | “One tuned model, for this business question.” |
| `…/decision_impact` | “What actions would that model have taken, vs the Lab.” |
| `…/calibration` | “Are the probabilities honest?” (synthetic only) |
| `…/segment_comparison` | “Does the winner hold in slices and over time?” |
| `artifacts/experiments/<uuid>/` | “One Lab training run’s leftover files.” Usually from tests, not from you clicking around as a customer. |
