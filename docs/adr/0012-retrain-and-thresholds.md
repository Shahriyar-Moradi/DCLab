# ADR 0012 — Retrain on newest data and editable monitoring thresholds

**Status:** Accepted (founder, 2026-10-17: "yes to all" on D1–D4)  
**Date:** 2026-10-17  
**Prompts:** D5a (retrain, backend), D5b (thresholds, backend), D5c (web)  
**Depends on:** [ADR 0006](0006-ml-state-graph.md) §2 (refs, Rev 2 re-baseline companion moves),
§3 (split plans), §5 (decision records); [ADR 0007](0007-improve-loop.md) (one final test per run);
monitoring (P7.4-A), usage limits (P8.4-A).

## Context

Monitoring finds drift but "never retrains or switches". The demo shows a retrained model beside the
model in use "on the same test" with Switch / Keep. Facts that constrain the design: a branch can never
change the dataset (new data is a new ROOT run); a split plan fixes the final test of ONE dataset
version, so a retrained model has its own final test; a champion must share the current champion's
split plan unless the same decision also moves `split_plan` (re-baseline). "Same test" is therefore
never literally true.

## Decisions

**D1 — the labelled file.** A person uploads the newer labelled file (`POST /v1/datasets`, purpose
training). The system never builds training data from scored files and outcomes. The rule only uses
files a person uploaded (never one a service token uploaded); a person starting a retrain may pick any
labelled file of the project uploaded after the training file of the model in use.

**D2 — policy.** Per project `off` (default) / `ask` / `auto` (`retrain_policies`), set only by a
workspace owner or administrator, with a reason and `If-Match`; every change is a
`retrain_policy_changed` History entry. The worker rule fires when the newest week of the model in use
is an alert AND a newer labelled file exists AND no retrain used that file or that week yet, at most once
per project in any 7 days: `ask` writes an Inbox question "Retrain on newest data?" (no run); `auto`
starts the retrain acting as the person who chose `auto`, re-checked each time (still an active owner or
administrator with ML-write; platform staff can never set the policy, so nothing runs as them). The rule
only trains on a file uploaded by someone who can start runs (never a service token's upload or an
external reader's upload into the Labs project). Automatic only STARTS
a retrain; switching is always a person. `RETRAIN_ENABLED=false` is the kill switch.

**Retrain run.** `retrain_service.start_retrain` queues a root run (same envelope, quota and run
capacity as `POST /v1/experiments`) on the newer file with what a root run accepts from the model in
use: its target and its goal (ProblemSpec: metric and constraints). Branch settings of the model in use
(families, class weighting, column treatments, a branch objective) are NOT carried: a root run
re-decides them, and re-runs its leakage plan, on its own training rows; the status read lists them as
`not_carried`, and a person can branch the retrained run with them. One unfinished retrain per project.
`POST /v1/projects/{id}/retrain` is people-only (ML-write): a service token is refused even with
`experiments:write`, because a retrain challenges the model in use and opens a switch proposal (a token
can still start its own runs). An automatic start refused by a plan limit pauses the rule for that project for 24
hours (`auto_paused_until` on the policy read; a person saving the policy clears it).

**D3 — comparison protocol.** After the retrained run locks its evidence, the worker stores ONE
comparison (`retrain_comparisons`, append-only, one per retrain):

1. The challenger's figures are its own single final test (`evaluation_metrics`). Comparable only when
   both runs have the same task and that final test is the latest chronological slice of the
   challenger's file (a `temporal_future` holdout); otherwise the challenger's test is mixed in time
   with its own training rows while the incumbent is scored out of time ("not the latest months").
2. The model in use is scored on the challenger's final-test rows ONLY when every one of those rows is
   strictly later than every TRAINING row of the model in use (its split map's training partition, the
   rows its package was fit on), on the challenger's split time column. Dates on both sides are read
   with the one format of the incumbent's training rows. An equal time is not later; any unreadable
   time on either side, or a date read as a number on one side, is not comparable. Then no final-test
   row was ever seen by the model in use: the comparison is leak-free for it.
   One retrain per model in use and file: a second challenger is never judged on the same final test.
3. It scores through the batch-scoring path (its own package, its own locked threshold) and the figures
   come from the monitoring outcome function, only when both models code the labels identically.
4. Otherwise the stored result is "not comparable: the new final test overlaps the training period of
   the model in use" (or the other closed reasons). The wording is always "same kind of test, latest
   months", never "same test".
5. Beside it, the real precision/recall of the model in use from linked outcomes, labelled "not the same
   data".

Nothing is fit or selected on these rows; the comparison is evidence for a person. Service tokens never
see the comparison figures (final-test values), only the state.

**Switch / Keep.** With the comparison the code-owned rule `retrain.challenger.v1` opens a
`champion_promoted` proposal moving `dataset_version` + `split_plan` + `feature_recipe` +
`champion_model` together (the ADR 0006 Rev 2 re-baseline); it cites the challenger's final evaluation.
This is the only rule allowed to call `propose_ref_move` (rules still never accept, reject or move refs).
Switch = a person accepts it through the existing approval (`POST /v1/projects/{id}/refs/champion_model`
with `proposal_id`, package check and release included); Keep = a person rejects it. No proposal opens
when the model in use changed meanwhile.

**History.** Retrain events are read projections in `/v1/activity` (`retrain_requested`,
`retrain_compared`), never fake decision records; the Inbox shows `retrain_ask` and `challenger_ready`.

**D4 — thresholds (implemented in D5b).** Watch / alert PSI and precision / recall floors per project,
editable by workspace owners and administrators only, within fixed bounds, with a reason (History
entry), applying to windows computed after the change (no recompute of past weeks except the existing
outcomes-link path). Floors raise alerts only when outcomes are linked and judged.

- Storage: `monitoring_thresholds`, append-only versions per project (the newest is in force; no row =
  the defaults PSI 0.10 / 0.20, no floors), each naming its `monitoring_thresholds_changed` History
  entry. `GET /v1/projects/{id}/monitoring-thresholds` (workspace read; tokens with `read`, no user
  ids); `PUT` with a reason, `If-Match` (version) and `Idempotency-Key`, owners and administrators
  only (never platform staff, never a token: the rule of the retrain policy). The same values again
  are `409 thresholds_unchanged`. A change queues no recompute.
- Bounds (API and DB CHECK): PSI watch 0.05–0.5; alert above watch, at most 1.0; each floor 0.01–0.99
  or null (off). Significance level, flagged-share lines and minimum rows / labels stay code-owned.
- Which values a computation uses: a window that already has a revision keeps the values stored on
  its newest one (`monitoring_windows.thresholds_used`; null = computed before 0089 = the defaults)
  when the week had ended when the job was queued or no scoring of the week completed since that
  revision. So a past week never changes, also when linked outcomes recompute it, and a looser edit
  never hides an alert the open week already raised without new scored data; only new scored data
  (or a first computation) picks up the values in force. Every new revision stores the values it used.
- Floors (binary models, real outcomes at the locked threshold): judged only when the window's
  outcomes are `measured` (minimum labelled rows and rows per class; the newest measured outcome file
  is used, so a small newer file never erases a measured week) and the floor's own rows (flagged
  labelled rows for precision, labelled positives for recall) number at least the minimum per class.
  A floor is `below` only when a one-sided exact binomial test (successes = true positives) gives
  p strictly below 0.01 / floors set; at or above the floor is `ok`; otherwise `not_judged` (not
  measured, not binary, counts unavailable, too few rows, or under the floor within chance).
- A floor judged below makes the week an `alert`. It is not drift: the summary code is `below_floor`
  unless drift alerted too (the summary still mentions watch-level drift), the finding is
  `performance_below_floor` when no drift alert, the notification headline says real outcomes fell
  below a floor, and the retrain rule (D2) still fires on it with the status state `below_floor`
  instead of `drift_found`.
- Error rates: inputs, scores and floors are each gated at 1% (family-wise within each), so one
  computation's worst-case false-alert rate is about 3%. The open week is recomputed when scorings
  or outcome links arrive, and each recompute looks again (the drift gate has the same property), so
  across a week's revisions the chance of a false alert is higher than per computation.

## Consequences

- No AI anywhere in the path: every step works with AI off.
- A retrain spends compute and counts against plan limits like any run.
- A comparison is often "not comparable" when the new file mostly repeats old months; that is the honest
  answer. The real outcomes beside it remain available.
- Three additive tables (migration `0087_retrain_policy`); downgrade drops them (runs, proposals and
  History entries stay).
- D5b: one additive table and one nullable column (migration `0089_monitoring_thresholds`); downgrade
  drops them (thresholds fall back to the defaults; History entries stay).
