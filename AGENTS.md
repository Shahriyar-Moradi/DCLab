# Base44 Dev Environment — DCLab

## Stack
- **Backend**: FastAPI (`apps/api/app/main.py`), uvicorn on port 8001 (internal). Python 3.12.
- **Frontend**: Next.js 15 (`apps/web`), dev server on port 3001, mapped to host port 3000.
- **Database**: PostgreSQL 16 (compose service `postgres`).
- **Pattern**: Single-origin BFF — browser talks only to the Next.js origin; `/api/backend/[...path]` proxies to FastAPI. Auth uses HttpOnly session cookies.

## Running
```bash
docker compose -f docker-compose.base44.yml up -d --build
```
Services: `postgres`, `migrate` (one-shot alembic), `seed` (one-shot demo users), `api`, `web`.

## Key setup details
- The API image (`Dockerfile.base44.api`) installs deps including `[boosting]` extras (xgboost, lightgbm, catboost) and `psycopg[binary]` (psycopg3 — SQLAlchemy 2.1 defaults to it).
- `ML_JOB_DISPATCHER=thread` so CSV uploads train in-process (no separate worker needed).
- `DCLAB_API_URL=http://api:8001` tells the Next.js BFF/middleware how to reach FastAPI internally.
- `allowedDevOrigins` in `next.config.mjs` uses `BASE44_PUBLIC_HOST_SUFFIX` so the preview origin can load dev assets/HMR.
- Source is bind-mounted into both `api` and `web` services for live reload.

## Demo logins (seeded by `dclab user seed`)
- Admin: `admin@dclab.io` / `AdminPass123`
- Developer: `developer@dclab.io` / `DeveloperPass123`
- Client: `demo@client.io` / `ClientPass123`

## No external secrets required
OpenAI integration (`decision_agent_enabled`, `pipeline_llm_verifier_enabled`) is off by default. No credentials are needed to boot.

## Verification
- `curl localhost:3000/` → 200, title "DCLab — Decision Intelligence"
- `curl localhost:8001/health` → `{"status":"ok","db":"connected"}`
- Login at `/login` with demo credentials to access `/admin`, `/app`, `/lab` areas.
