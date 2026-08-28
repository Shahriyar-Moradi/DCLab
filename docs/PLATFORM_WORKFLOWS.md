# How the platform works — simple workflows

This is a plain-language map of **who does what**, **where data goes**, and **how
a recommendation is made**. It is not a technical spec.

For clicking through the live app, see `docs/HOW_TO_USE_THE_PLATFORM.md`.
For folder names, see `docs/DIRECTORY_GUIDE.md`.

---

## 1. The big picture (one sentence)

A business user brings in **deals** (or tries a Lab problem). The engine **scores**
them. A **policy** picks an action. A **translator** turns that into everyday
language. The customer sees only the translation. Staff can still open the full
lab notebook.

```
You  →  Website  →  API  →  Engine + policy  →  Translator  →  Screen
                              ↓
                         Database (facts)
                         Admin registry (raw ML, staff only)
```

There are two logins:

| Person | Sees |
|---|---|
| **Customer** (`client_user`) | Dashboard, Insights, Opportunities, Decisions, Upload, Labs — business words only |
| **DCLab staff** (`dclab_admin`) | All of the above, plus Organizations, Labs & Experiments, Registry, Monitoring — full ML detail |

---

## 2. User workflow — visitor (not logged in)

1. Opens http://localhost:3001.
2. Can read marketing pages (Company, Platform, Pricing, …).
3. Clicks **Sign in** → `/login`.
4. If they try `/app/...` or `/admin/...` without a login, the website sends them
   back to login.

Nothing is scored until someone is signed in.

---

## 3. User workflow — customer (the main path)

This is the path you should learn first.

```
Sign in
   → Dashboard          (what is going on?)
   → Upload (optional)  (bring in deals)
   → Opportunities      (browse deals)
   → Generate decision  (ask: what should we do?)
   → Decisions          (read the ledger)
   → Insights           (see themes by business area)
   → Labs               (try a bounded problem on sample/small data)
```

### Step by step

1. **Sign in** as `demo@client.io`.
2. **Dashboard** shows counts and recent recommendations. It only lists
   *business decisions*, never “a model finished retraining.”
3. **Upload** a CSV of deals, or skip this — the demo database already has
   sample opportunities.
4. **Opportunities** is the list of deals (amount, stage, customer, …).
5. Open one deal and click **Generate Decision**. That is the core moment:
   score → recommend an action → explain it in plain sentences.
6. **Decisions** is the history of those recommendations (Contact today,
   Send an email, No action needed, …).
7. **Insights** groups other engine questions (churn, campaign, …) by
   department: Marketing, Sales, Revenue, Churn & Retention, and so on.
8. **Labs** lets you try one fixed problem a few times (sample data or a
   small file). Same kind of answers, with limits so it cannot run forever
   or swallow a huge file.

A customer who types an admin URL gets **Forbidden**. That is the product
working, not a bug.

---

## 4. User workflow — staff (admin)

Staff sign in as `admin@dclab.io`. They can use the customer app (to support
an account) **and** the admin app.

```
Sign in
   → Labs & Experiments   (train/compare models on a dataset)
   → Registry             (every model the lab produced, including client trials)
   → Monitoring           (what ran, and how scores moved)
   → Organizations        (which workspace, how much activity)
```

Typical staff loop:

1. Load or pick a **dataset**.
2. Define or pick a **task** (what to predict).
3. Run an **experiment** (try many models, keep the useful ones).
4. Read the report: candidates, scores, ensemble.
5. If a customer ran **Client Labs**, open that trial in **Registry** and
   see the raw result the customer never saw.
6. If a customer dropped a file in a Labs **open ingest** box, check
   `Labs & Experiments` → "Open ingest jobs" (or `/admin/client-uploads/{id}`)
   for the auto-train job's EDA, target choice, and candidate scores.

---

## 5. Data workflow — from a spreadsheet to a saved deal

This is what happens when you upload opportunities.

```
CSV file on your computer
   → Website upload page
   → API checks columns and rows
   → Valid rows become Opportunity records in Postgres
   → Invalid rows come back with a reason (missing field, bad date, …)
   → Opportunities list reads from that table
```

Important facts:

- Uploads belong to a **workspace** (this demo uses one default workspace).
- The CSV is not the live app database. After upload, Postgres is the source
  of truth.
- Sample file: `data/sample/opportunities.csv`.
- Other folders named `data/` (sim, case studies, Olist) are **files on disk**
  for training and research. They are not the same as the opportunities table
  you browse in the UI.

---

## 6. Decision workflow — the core product

This is the most important machine on the platform.

```
You click “Generate Decision” on a deal
   → API loads that Opportunity
   → Predictor scores “how likely is this to convert?”  (kept inside)
   → Policy uses that score + deal facts + costs
        and picks an action (contact / email / wait / …)
   → Both the raw score and the decision are saved
   → Translator rewrites the answer:
        High / Medium / Low
        “Contact today”
        a few plain reasons
   → You see only the rewritten answer
```

What is stored vs what you see:

| Stored (inside) | Shown to the customer |
|---|---|
| Conversion probability | Confidence band (High / Medium / Low) |
| Model version | Not shown |
| Internal action key | Human label (“Send an email”) |
| Feature-style evidence | Short sentences about engagement, stage, recency |

You can generate again later; the saved decision is updated. The list on
**Decisions** is this table, always passed through the translator on the way out.

---

## 7. Insights workflow

Insights are **not** a second copy of Opportunities.

```
Staff (or a prior sim run) produced SimulationRun rows
   → Insights page asks: “latest result per problem”
   → Translator turns each into the same insight shape
   → Page groups them: Marketing, Sales, Revenue, Churn, …
```

Empty groups mean “this problem has not been run yet,” not an error.

---

## 8. Client Labs workflow (try a problem)

```
You pick a problem (e.g. retention)
   → Optional: upload a small CSV (max 500 rows) or use sample data
   → Quota check (max 3 runs per problem per workspace)
   → Same simulation engine the staff use, in a time box (~30 seconds)
   → On success:
        customer store = translated insights only
        staff store   = full raw result (audit), for Registry
   → You read Insight cards
   → Staff can open Registry → that client trial
```

If the file is wrong, too big, or the run is too slow, you get a **failed**
run with a message — not a crashed website.

Separate path — **open ingest** (top of each category on `/app/labs`):

```
You drop any usual data file (no required field names)
   → File is saved as-is (500 rows / 2 MB bound)
   → Customer sees kind + fields noticed, never the disk path
   → No trial quota, no ClientLabRun/audit payload — you see nothing more
   → Behind the scenes, admin-only (skipped for logs/headerless/too-small files):
        EDA → heuristic target → missing-value decisions → ColumnTransformer
        → train/test + K-fold → RandomForest/XGBoost → a real Experiment
        see "Simple-case auto-train" in docs/LABS_DATA_UNDERSTANDING.md
   → Structuring messy files (language tools + DCLab reading pipeline) = TODO
     see docs/LABS_DATA_UNDERSTANDING.md
```

---

## 9. Admin experiment workflow (the Lab)

```
Dataset in the Lab
   → Task: “predict X from these columns”
   → Engine tries many model + feature-group combinations
   → Drops leaky or weak copies
   → May blend survivors if the blend beats the best single
   → Writes artifacts to disk + a report in the database
   → Experiments page shows the full scientific detail
```

This path never appears on the customer dashboard.

---

## 10. Login and permission workflow

```
Email + password
   → API checks the user table
   → Issues a signed token (JWT)
   → Website stores it in a cookie
   → Every later request sends that token
   → API: missing token → 401, customer on /admin → 403
   → Website: same rules in middleware (so typing a URL is not enough)
```

Hiding a menu item is extra. The lock is on the **API** and the **website
gate**, not only the nav bar.

---

## 11. Translation workflow (why two “languages”)

The engine speaks ML (probabilities, AUC, model names). The customer product
must not.

```
Raw engine output
   → translation/decisions.py     (deals)
   → translation/simulations.py   (Labs / insights)
   → ClientFacingInsight
         who it is about
         which business area
         headline
         High / Medium / Low
         recommended action
         expected value
         2–4 sentences
```

Four safety nets try to keep it that way: schema scan, frontend scan, live
API crawl, live “customer cannot open admin” crawl. Those run in CI.

---

## 12. How the pieces sit together

```
                    ┌──────────── public site ────────────┐
                    │  homepage, pricing, company, …      │
                    └───────────────┬─────────────────────┘
                                    │ Sign in
                    ┌───────────────┴─────────────────────┐
                    │              /login                  │
                    └───────────────┬─────────────────────┘
              customer              │                staff
                    │               │                   │
                    ▼               │                   ▼
             /app/dashboards        │            /admin/lab
             /app/opportunities     │            /admin/models
             /app/decisions         │            /admin/monitoring
             /app/insights          │            /admin/organizations
             /app/labs              │
                    │               │                   │
                    └─────── API ───┴───────────────────┘
                                    │
                    ┌───────────────┴─────────────────────┐
                    │  Postgres: users, deals, decisions,  │
                    │  experiments, lab runs, audits       │
                    │  Disk: configs YAML, trained models, │
                    │  experiment artifacts                │
                    └─────────────────────────────────────┘
```

---

## 13. What to remember

1. **Deals in → recommendation out** is the customer product.
2. **Score then policy then translate** is always the order. Customers only
   see the last step.
3. **Labs (customer)** is a short, limited try of the same engine staff use.
4. **Labs & Experiments (admin)** is the unrestricted research tool.
5. **Postgres** holds live app data. **`data/` folders** hold files used to
   train and simulate. **`models/`** holds trained files. **`artifacts/`**
   holds experiment dumps. Do not mix them up.

If you only do one loop on a new machine: sign in as the customer → open a
deal → generate a decision → read it on Decisions.
