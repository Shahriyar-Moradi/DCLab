# DclabScheduledRunsMissed — weekly scoring is late

**Symptom.** At least one enabled weekly scoring schedule is more than 15 minutes past its slot
(`scoring_schedules.next_planned_for`) for 30 minutes. Customers miss this week's scored list.

**Likely causes.**
- No worker running (the tick runs inside `dclab worker run`, every `SCORING_SCHEDULE_TICK_SECONDS`).
- The tick fails every time (`dclab_scheduled_ticks_total{outcome="error"}` rising; worker log
  lines `scoring schedule tick: ...`).
- Platform kill switch: `SCORING_SCHEDULE_ENABLED=false` makes every run wait ("paused by the
  platform"); expected while it is off.

**Checks.**
1. Worker alive: see [worker-heartbeat.md](worker-heartbeat.md).
2. `docker compose -f docker-compose.base44.yml logs --since 2h worker | grep "scoring schedule"`.
3. psql: `SELECT id, workspace_id, next_planned_for FROM scoring_schedules WHERE enabled AND
   next_planned_for < now() - interval '15 minutes' ORDER BY next_planned_for LIMIT 20;`
4. Recent outcomes: `SELECT outcome, reason_code, count(*) FROM scoring_schedule_runs WHERE
   created_at > now() - interval '1 day' GROUP BY 1, 2;` (skipped slots carry a reason code).

**Safe actions.**
- Restart the worker: `docker compose -f docker-compose.base44.yml restart worker`. Each slot is
  handled once (`UNIQUE (schedule_id, planned_for)`), so a catch-up tick never double-scores.
- Do not edit `scoring_schedules` rows by hand; people change schedules in Studio.

**Escalation.** Ticks keep failing after a restart: page the backend on-call with schedule ids and
reason codes (no file names or predictions).
