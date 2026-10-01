# Simulation archive isolation and forward repair

Owner: DCLab platform operator and data-governance reviewer. Applies to
`simulation_runs.workspace_id IS NULL` rows created before the tenant-aware
`0058_simulation_workspace` migration (ADR 0004). These rows are an archive,
not a default-workspace dataset. There is no approved automatic backfill,
customer export or deletion; legacy-route removal follows only the
compatibility window below.

## Compatibility window

The legacy archive surfaces (`simulation_runs`, `/admin/simulations/*`,
`/app/insights` and the translated Decision.ai opportunities, decisions,
insights and dashboards surfaces) are **frozen now** (2026-10-01): no new
features, and tenant filtering stays mandatory. The removal-or-rework decision
is made at the Phase 9 business decision layer rework
([roadmap](../mvp/ROADMAP.md)); the **hard deadline** is removal or formal
re-homing no later than **2027-03-31**. Until then URLs and response shapes
stay stable with mandatory tenant filtering, no feature flag guarding these
surfaces is removed, and the pre-0058 global read is never restored. Archive
recovery of null-owner rows is not part of that decision: it stays a
separate, reviewed work order under the procedure below. Policy record:
[S0-P04D gate](../verification/S0_P04D_RETIREMENT_ISOLATION_GATE.md).

## Contain a suspected leak

1. Record the deployment SHA, time, affected routes, request IDs and validated
   workspace IDs in the incident ticket. Do not copy raw payloads into the
   ticket, chat, logs or metrics.
2. Disable the affected `/app/insights`, simulation or derivative read route at
   the application/ingress boundary, or restore the last verified
   **tenant-aware** binary while retaining the additive 0058 columns. Never
   deploy a pre-0058 global reader or use a schema downgrade as feature
   rollback.
3. Confirm foreign and null-owner IDs return 404 and current membership denial
   takes effect on the next request. Check protected operator telemetry for
   bounded denial counts and fixed-action admin audit events; do not use
   customer or subject IDs as metric labels.

## Read-only inventory

Use an audited, read-only database identity on a restored backup or approved
maintenance connection. Do not inspect payloads unless the incident ticket
authorizes it. Record the count and a digest of the exact row-ID list; paged
queries must use a stable `(created_at,id)` order.

```sql
SELECT count(*) FROM simulation_runs WHERE workspace_id IS NULL;
SELECT id, created_at, use_case, model_version, policy_version
FROM simulation_runs
WHERE workspace_id IS NULL
ORDER BY created_at, id
LIMIT 100;
```

The second query is an inspection example, **not** ownership evidence. Use
`id`-keyset pagination to enumerate more rows; do not increase the limit into
an unbounded payload export. The original 0003 table had no tenant or project
foreign key. Use case, subject, timestamp, payload label, current user and
`DEFAULT_WORKSPACE_ID` cannot establish ownership.

## Propose a reviewed import, never an ad-hoc UPDATE

1. Obtain an independent source-of-truth record that maps each exact
   `simulation_runs.id` to one existing `workspaces.id`, with provenance,
   collection time, retention basis and a mapping digest. Put ambiguous rows
   on a reject list that stays null. Check legal/immutability holds.
2. Obtain two independent approvals: a platform operator for the technical
   mapping and a data-governance reviewer for ownership/retention. Neither
   approval may be inferred from the current database row alone.
3. Take and verify a restorable database backup. Run a dry-run query comparing
   proposed IDs with live null-owner rows, duplicates, missing workspaces,
   cross-tenant project links and changed scientific payload digests. Require
   zero unexpected rows. Record the row count and mapping digest, not raw data.
4. Create a **separate** additive, idempotent migration/work order pinned to
   that approved mapping. Its transaction must update only rows still null
   and still matching the reviewed identifiers/digests; changed or disputed
   rows abort without partial attribution. Record actor, ticket, mapping
   digest, before/after ownership and reason in protected audit. No direct
   production SQL update is authorized by this runbook.
5. Re-run the previous-head migration, two-workspace/foreign-ID, null archive,
   event audience, browser BFF and scientific-digest tests. Validate the same
   mapping on a second run changes zero rows. Only then re-enable affected
   routes for an allowlisted canary.

If proof or approval is missing, leave the rows null and hidden. On failure,
repair forward with the 0058 schema and a tenant-aware reader; do not make a
global-read or raw-output fallback. Retain the backup and audit according to
the incident's approved retention policy.
