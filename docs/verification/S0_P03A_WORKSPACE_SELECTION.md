# S0-P03A workspace selection contract

**Status:** CURRENT  
**Plan/prompt:** S0-P03A  
**Canonical current head:** [`truth_baseline.json`](../../contracts/truth_baseline.json)
**ADR:** [0003-workspace-selection.md](../adr/0003-workspace-selection.md)

Browser and API share one membership-checked active workspace. The selector
is presentation context: every tenant route still runs
`resolve_workspace_access`. The Python client keeps explicit `X-Workspace-Id`
and does not inherit the browser session.

## Contract

| Memberships | Login default | Tenant route without selector |
| --- | --- | --- |
| 0 | `selected_workspace_id` null | 403 `not authorized for a workspace` |
| 1 | that workspace | that workspace |
| N, home still active | home | home |
| N, home unset | null until `PUT /auth/workspace` | 400 until header or PUT |

- Persist `auth_sessions.selected_workspace_id` only after
  `workspace_is_selectable`.
- Browser also sends `X-Workspace-Id` (BFF forwards it). Header wins; an
  unauthorized header is still 403.
- Stale session selection (removed or suspended membership) is ignored and
  never proves access.
- Switching calls `PUT /auth/workspace`, then `clearWorkspaceQueries`.
- Mutation surfaces show `ActiveWorkspaceNotice`.
- `dclab_client` stays constructor-scoped; it does not call `/auth/workspace`.

Rollback for the additive column is `alembic downgrade` through
`0057_session_workspace` (see the ADR). Current product head is owned by
canonical truth, not this document.

## Evidence

```text
Plan/prompt ID: S0-P03A
Claim: Browser and API share one membership-checked active workspace; zero/one/many memberships; session persistence and restoration; membership removal cannot prove access; query cache re-key; Python client stays explicit
Status: VERIFIED (API + source + drift; local Playwright NOT_TESTED this prompt)
Commit/image digest: uncommitted working tree on top of 91986b9
Environment: local macOS, .venv CPython 3.12, Postgres 16 on localhost:5432
Migration path tested: none this prompt (column already exists from 0057_session_workspace)
Commands:
  .venv/bin/pytest -q --tb=short apps/api/tests/test_workspace_selection.py packages/dclab_client/tests/test_http_contract.py
  .venv/bin/pytest -q --tb=line
  .venv/bin/python -m scripts.generate_truth_artifacts
  .venv/bin/python -m scripts.check_truth_drift
  ./apps/web/node_modules/.bin/tsc --noEmit -p apps/web/tsconfig.json
Expected result: selection tests green including membership removal and login restoration; full pytest green after artifact refresh; drift clean; tsc clean
Observed result: PENDING_FULL_GATE
Artifact/log/dashboard link: docs/adr/0003-workspace-selection.md
Security and tenant checks: header cannot grant a foreign workspace; removed membership 403; cross-workspace project ids stay 404; suspended memberships are not selectable; bearer cannot use PUT /auth/workspace
Rollback/kill switch: revert this tree; drop selected_workspace_id via downgrade of 0057_session_workspace; AUTH_BROWSER_SESSIONS_ENABLED=false still stops cookies
Known limitations: capability matrix and 403-vs-404 standardization remain S0-P03B; local Playwright not run in this agent
Reviewer/date: S0-P03A / 2026-09-12
```

The 2026-09-11 local browser suite (18/18, including the visible selector)
remains prior evidence; this prompt added membership-removal and session-
restoration tests on the same contract.

## Next prompt

**S0-P03B** — central capability authority. Do not start it from this prompt.
**S0-P04A** already exists in this working tree.
