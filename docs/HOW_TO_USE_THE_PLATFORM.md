# How to start and use the platform (beginner)

This is the current, working walkthrough. Older notes that mention `/dashboards` or
`/lab` without `/app` or `/admin` are out of date — those URLs moved after the
login/role split.

---

## What you need

- This project folder: `decision_ai`
- Python virtualenv at `.venv` (already created on this machine)
- Postgres 16 running locally (Homebrew)
- Two terminals (or let an agent start the servers for you)

You do **not** need Docker for daily use.

**Ports (configured, not hardcoded in one place):**

| App | Port | Config |
|---|---|---|
| Backend (API) | **8001** | `API_PORT` in repo-root `.env`; `make run` |
| Frontend (website) | **3001** | `WEB_PORT` in `.env`; `NEXT_PUBLIC_API_URL` in `apps/web/.env.local` |

The website calls `http://localhost:8001` for every API request. The API allows
that website origin via `CORS_ORIGINS`. Copy `.env.example` → `.env` and
`apps/web/.env.example` → `apps/web/.env.local` if those files are missing.

---

## 1. Start the machines (first time on a new computer)

From the project root:

```bash
cd /Users/shahriar/Downloads/decision_ai
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp -n .env.example .env
make db
make migrate
make train
```

`make train` only needs to run once (or after you change the M1 training data).
It writes files under `models/revenue_prediction/`.

Create the two demo logins (skip if they already exist):

```bash
source .venv/bin/activate
dclab user create --email demo@client.io --password 'ClientPass123!' --role client_user --name Demo
dclab user create --email admin@dclab.io --password 'AdminPass123!' --role dclab_admin --name Admin
```

If it says the email already exists, you are fine — use the passwords below.

---

## 2. Start the backend (API)

**Terminal 1**, from the project root:

```bash
cd /Users/shahriar/Downloads/decision_ai
source .venv/bin/activate
make run
```

Wait until you see something like `Uvicorn running on http://127.0.0.1:8001`.

Check: open http://127.0.0.1:8001/health — you should see
`{"status":"ok","db":"connected"}`.

Leave this terminal running.

---

## 3. Start the website (frontend)

**Terminal 2**, from the project root:

```bash
cd /Users/shahriar/Downloads/decision_ai
make web
```

That runs `npm --prefix apps/web run dev`. Wait until it says it is ready on
port 3001.

Check: open http://localhost:3001/login — you should see the Sign in form.

Leave this terminal running too.

If port 3001 is already in use, either use the site that is already there, or
stop the old process and run `make web` again.

---

## 4. Open the app and sign in

Go to **http://localhost:3001/login**.

Use **one** of these accounts (not both at the same time in the same browser
unless you use a private window for the second):

| Who | Email | Password | Lands on |
|---|---|---|---|
| **Begin here (customer)** | `demo@client.io` | `ClientPass123!` | `/app/dashboards` |
| DCLab staff | `admin@dclab.io` | `AdminPass123!` | `/admin/lab` |

Click **Sign in**. If it fails, the API is not running — go back to step 2.

---

## 5. Use it as a customer (the simple path)

After login as `demo@client.io`, the top **Workspace** links are:

Dashboard → Insights → Opportunities → Decisions → Upload → Labs

Do them in this order the first time.

### 5.1 Dashboard — `/app/dashboards`

Your home base. Live counts of opportunities and decisions, plus a recent
decisions feed in business language (High / Medium / Low confidence, not raw
model scores).

### 5.2 Insights — `/app/insights`

Recommendations grouped by business function (Marketing, Sales, Revenue,
Churn & Retention, …). Empty groups are normal if that simulation has not been
run yet.

### 5.3 Upload (optional) — `/app/opportunities/upload`

This database already has sample deals (~520). You can skip upload the first
time.

To try it: choose `data/sample/opportunities.csv` from the project folder and
upload. You will get a count of inserted vs rejected rows.

### 5.4 Opportunities — `/app/opportunities`

The list of deals. Sort, filter, click a row.

### 5.5 Generate a decision — `/app/opportunities/[id]`

This is the core product moment:

1. Open any opportunity.
2. Click **Generate Decision** (if it does not already have one).
3. The API scores the deal and returns a recommended action such as
   **Contact today**, **Send an email**, or **No action needed**, with a
   confidence band and short reasons.

### 5.6 Decisions — `/app/decisions`

The ledger of every recommendation. Click a row for the full reasoning.

### 5.7 Client Labs — `/app/labs`

A bounded trial: pick a problem (for example retention), run with **sample
data** (easiest) or a small CSV that matches that problem. You get the same
style of insights. Limits: max 3 runs per problem, max 500 uploaded rows,
~30 second budget.

Above each category there is also a **No template required** box. Drop any usual
data file (spreadsheet, JSON, table file, Excel, or a raw log) — we do **not**
require particular field names. That save does not run a trial. Turning messy
files into a usable table is not built yet (`docs/LABS_DATA_UNDERSTANDING.md`).

---

## 6. Use it as an admin (optional, second browser)

Log out, or open a private window, and sign in as `admin@dclab.io`.

You will see the client Workspace **plus** admin links:

| Page | URL | What you see |
|---|---|---|
| Labs & Experiments | `/admin/lab` | Datasets, tasks, experiments — full ML detail |
| Organizations | `/admin/organizations` | Workspaces and counts |
| Registry | `/admin/models` | Models from experiments, simulations, client trials |
| Monitoring | `/admin/monitoring` | Retrain events and dataset health |

If you run a Client Labs trial as the customer, then log in as admin, that run
can appear on Registry as a **client trial** with the raw payload the customer
never saw.

A customer who types `/admin/lab` gets **403 Forbidden**. That is intended.

---

## 7. Stop the servers

In each terminal: `Ctrl+C`.

Postgres can stay running (`brew services` keeps it in the background).

---

## If something is broken

| Symptom | Fix |
|---|---|
| Login page loads but Sign in fails | API is down. `make run` in terminal 1. Check http://127.0.0.1:8001/health |
| `connection refused` on :8001 | Same — start `make run` |
| `database "decisionai" does not exist` | `make db` then `make migrate` |
| Generate decision errors about a missing model | `make train` |
| Website blank / old pages | Stop `make web`, from `apps/web` run `rm -rf .next`, then `make web` again |
| Port 3001 already in use | You already have a frontend. Use it, or quit that Node process and restart |
| `alembic` “No script_location” | Run `make migrate` from the **project root**, not from `apps/api` |

---

## Quick URL map (after login)

| You want to… | Open |
|---|---|
| Sign in | http://localhost:3001/login |
| Customer home | http://localhost:3001/app/dashboards |
| Deals | http://localhost:3001/app/opportunities |
| Recommendations | http://localhost:3001/app/decisions |
| Try a problem | http://localhost:3001/app/labs |
| Staff lab | http://localhost:3001/admin/lab |
| API health | http://127.0.0.1:8001/health |
| API docs (staff/debug) | http://127.0.0.1:8001/docs |
