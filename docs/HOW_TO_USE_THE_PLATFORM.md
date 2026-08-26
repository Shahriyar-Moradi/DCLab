# How to Use the Platform — Step by Step

This is a plain walkthrough of the actual app, in the order you'd normally click
through it. Both servers are already running for you right now:

- **Backend (API):** http://127.0.0.1:8000 — confirmed healthy
- **Website/app (frontend):** http://127.0.0.1:3000 — confirmed loading

Just open **http://localhost:3000** in your browser to follow along.

---

## Step 0 — If the servers aren't running

You won't need this right now, but for next time, from the project folder:

```bash
# Terminal 1 — backend
source .venv/bin/activate
make run

# Terminal 2 — frontend
make web
```

Then visit http://localhost:3000.

---

## Step 1 — The homepage

Go to **http://localhost:3000**. This is the public marketing homepage — it explains
what the product does. The top navigation bar has two groups of links:

- **Marketing pages** (Company, Solutions, Platform, Industries, Resources,
  Dashboards, Pricing) — informational pages.
- **Workspace** (Opportunities, Decisions, Upload, Experimentation Lab) — this is the
  actual working app. This is where you'll spend your time.

Click **"Sign In"** in the top right — in this build it takes you straight into
**Dashboards** (there's no real login system yet, this is a single-workspace demo).

---

## Step 2 — Dashboards

**http://localhost:3000/dashboards**

This is the home base once you're "in" the app. It shows real, live numbers pulled
from the database: how many opportunities exist, how many decisions have been made,
and a breakdown of which actions are being recommended most. Nothing here is
placeholder — it's querying the same API you'll use in every other step.

---

## Step 3 — Upload some opportunities (deals/leads)

**http://localhost:3000/opportunities/upload**

This is where you'd feed the system real sales data.

1. Click the upload box and choose a CSV file. There's a ready-made sample at
   `data/sample/opportunities.csv` in the project folder — use that one for a first
   try.
2. Click upload. You'll see a progress bar, then a result: how many rows were
   inserted successfully, and how many were rejected (with the reason for each
   rejected row, e.g. missing a required field).
3. Behind the scenes this calls `POST /opportunities/upload` on the backend, which
   parses the CSV and writes valid rows into the database.

---

## Step 4 — Browse opportunities

**http://localhost:3000/opportunities**

This is the list of every opportunity in the system (including whatever you just
uploaded). You can:

- Sort by creation date or deal amount
- Filter by stage
- Page through results (20 per page)

Click any row to open it.

---

## Step 5 — Open one opportunity and generate a decision

**http://localhost:3000/opportunities/[id]** (click into any row from Step 4)

This is the core "predict → decide" moment of the product. On this page:

1. You see the opportunity's details (customer, deal size, engagement score, etc.).
2. If no decision exists yet for it, click **"Generate Decision."**
3. The backend (`POST /decisions/generate`) runs the trained model on this specific
   opportunity, gets a conversion probability, applies the business policy (thresholds
   + expected value), and returns a recommended action — e.g. "contact today," "send
   email," or "no action" — along with the reasoning behind it.
4. That decision is now saved permanently and will show up in Step 6.

---

## Step 6 — Browse and review decisions

**http://localhost:3000/decisions**

This is the ledger of every decision the system has ever made — one row per
opportunity it scored. You can filter by status or by recommended action. Click into
any row to see the full reasoning trail for that one decision (probability, expected
revenue, confidence, and the plain-English reasoning behind the recommendation).

---

## Step 7 — The Experimentation Lab (the advanced part)

**http://localhost:3000/lab**

This is a separate, more advanced tool for training and comparing prediction models
on any dataset — not just opportunities. The Lab overview page shows four sections,
each with its own page:

### 7a. Datasets — `/lab/datasets`
Every dataset that's been loaded into the Lab (including, right now, the real Olist
e-commerce data and the synthetic benchmark datasets from the case-study work).
Click one to see its row/column counts and a data-quality profile (missing values,
column types) if one has been generated.

### 7b. Tasks — `/lab/tasks`
A "task" is a prediction problem definition — what to predict, from what data, over
what time horizon. `/lab/tasks/create` lets you load a new task from a YAML config
file already in the repo (e.g. `configs/tasks/purchase.yaml`).

### 7c. Experiments — `/lab/experiments`
Every model-search run that's actually been executed. Click one to open its full
report: which candidate models were tried, which were selected into the final
ensemble, their scores, and a downloadable-style markdown report explaining the whole
run.

### 7d. Running a new experiment
Right now, starting a brand-new experiment run is done from the command line (there's
no "Run" button in the UI yet):

```bash
source .venv/bin/activate
python -m app.cli.main experiment run --dataset <dataset-name> --task <task-slug>
```

It will then appear in `/lab/experiments` in the browser like any other run.

---

## Step 8 — The benchmark harness (for you, not for customers)

This part has no web page — it's a research tool that already produced its final
report. If you want to see the "single model vs. multi-model" comparison:

- Open `reports/case_study_scorecard.md` for the summary table.
- Open `docs/case-study-findings.md` for the full plain-language write-up.

To regenerate the scorecard yourself:

```bash
source .venv/bin/activate
python -m benchmarks.scorecard --out reports/case_study_scorecard.md
cat reports/case_study_scorecard.md
```

---

## Quick map of what you just used

| Page | What it does | API it calls |
|---|---|---|
| `/dashboards` | Live summary numbers | `GET /opportunities`, `GET /decisions` |
| `/opportunities/upload` | Upload a CSV of leads/deals | `POST /opportunities/upload` |
| `/opportunities` | Browse/filter/sort all opportunities | `GET /opportunities` |
| `/opportunities/[id]` | View one + generate its decision | `GET /opportunities/{id}`, `POST /decisions/generate` |
| `/decisions` | Browse/filter all decisions made | `GET /decisions` |
| `/decisions/[id]` | Full reasoning for one decision | `GET /decisions/{id}` |
| `/lab` | Lab overview | `GET /lab/environments`, `/lab/datasets`, `/lab/tasks`, `/lab/experiments` |
| `/lab/datasets` , `/lab/datasets/[id]` | Browse datasets + profile | `GET /lab/datasets`, `GET /lab/datasets/{id}/profile` |
| `/lab/tasks` , `/lab/tasks/create` | Browse/create prediction tasks | `GET /lab/tasks`, `POST /lab/tasks/from-config` |
| `/lab/experiments` , `/lab/experiments/[id]` | Browse model-search runs + full report | `GET /lab/experiments`, `.../report`, `.../candidates`, `.../comparison` |

Every one of these was individually checked today with live requests against the
running backend — all wired correctly, no broken connections.
