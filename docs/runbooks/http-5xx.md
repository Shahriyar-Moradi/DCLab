# DclabHttp5xxRateHigh — API errors

**Symptom.** More than 2% of API requests answered 5xx for 10 minutes. Studio shows errors; `/v1`
clients get `internal_error` / `unavailable` envelopes with a `request_id`.

**Likely causes.**
- Database unreachable or saturated (connections exhausted, long locks).
- A deploy with a failing route (one `route` label dominates).
- Object storage or an AI provider failing in a request path (AI calls refuse instead of failing;
  5xx there is a bug).
- Migrations not applied after a deploy (missing column errors).

**Checks.**
1. `curl -s localhost:8001/health/ready | python3 -m json.tool` (database, migrations, storage).
2. Which routes: `sum by (route) (rate(dclab_http_requests_total{status_class="5xx"}[5m]))`.
3. Correlate: a user's `request_id` (response header `X-Request-Id`) →
   `docker compose -f docker-compose.base44.yml logs --since 1h api | grep <request_id>`
   (`v1_error status=500 ... exception=<Type>` lines carry the exception type; with
   `LOG_FORMAT=json` the record has `request_id` and `trace_id`).
4. Migrations: `.venv/bin/alembic current` vs `.venv/bin/alembic heads` (exactly one head).

**Safe actions.**
- Roll back the last API deploy; restart `api` if it is wedged:
  `docker compose -f docker-compose.base44.yml restart api`.
- Apply pending migrations through the `init` job (`alembic upgrade head`), never by hand.

**Escalation.** Above threshold 20 minutes after a rollback, or any sign of cross-tenant data in an
error: page the backend on-call and the security owner immediately.
