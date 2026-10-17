import assert from "node:assert/strict";
import test from "node:test";
import {
  activitySentence, actorLabel, activityHref, aiIsOff, championMetric, championScoreText, cvScoreText, foldMeter, greeting, homeSubtitle, isAssistantRow, liveRunText, liveRuns,
  otherRunsText, runningCount, runningSentence,
  needsYouRow, recentProjects, runResultText, runsInProgress, startedAgo,
} from "./studio-home.ts";

const PROJECT = "11111111-1111-4111-8111-111111111111";
const RECORD = "22222222-2222-4222-8222-222222222222";
const base = { ai_enabled_setting: true, policy_unavailable: null, switches: { platform_ai_blocking: null } };

test("AI is off for every way it can be off; only off is ever stated", () => {
  assert.equal(aiIsOff({ ...base, ai_enabled_setting: false }), true);
  assert.equal(aiIsOff({ ...base, policy_unavailable: "policy_missing" }), true);
  assert.equal(aiIsOff({ ...base, switches: { platform_ai_blocking: "global_ai" } }), true);
  assert.equal(aiIsOff(base), false);
});

test("activity links need UUID ids and only point at decisions and runs", () => {
  assert.equal(activityHref({ kind: "decision", project_id: PROJECT, link: { kind: "decision_record", id: RECORD } }), `/projects/${PROJECT}/decisions?record=${RECORD}`);
  assert.equal(activityHref({ kind: "run_queued", project_id: PROJECT, link: { kind: "experiment", id: RECORD } }), `/projects/${PROJECT}/experiments/${RECORD}`);
  assert.equal(activityHref({ kind: "agent_run_started", project_id: PROJECT, link: { kind: "agent_run", id: RECORD } }), null);
  assert.equal(activityHref({ kind: "decision", project_id: "../x", link: { kind: "decision_record", id: RECORD } }), null);
  assert.equal(activityHref({ kind: "decision", project_id: PROJECT, link: { kind: "decision_record", id: "javascript:1" } }), null);
  assert.equal(activityHref({ kind: "decision", project_id: null, link: { kind: "decision_record", id: RECORD } }), null);
});

test("actor kinds map to rule / agent / person (you)", () => {
  assert.deepEqual(actorLabel({ kind: "rule" }), { text: "The rules", tone: "det" });
  // The read model never says who started an agent run (a person or an access token): never "the assistant", never a named starter.
  assert.equal(actorLabel({ kind: "agent", agent_run_id: "x" }).text, "An AI agent run");
  assert.match(actorLabel({ kind: "agent" }).text, /connected tool \(access token\) or an AI agent/);
  assert.doesNotMatch(actorLabel({ kind: "agent", agent_run_id: "x" }).text + actorLabel({ kind: "agent" }).text, /the assistant/i);
  assert.equal(actorLabel({ kind: "person", is_you: true }).text, "You");
  assert.equal(actorLabel({ kind: "person" }).text, "A person");
  assert.equal(actorLabel({ kind: "__proto__" }).text, "Someone else");
});

test("champion metric prefers the selection metric and is empty until evidence is locked", () => {
  const p = (champion: unknown) => ({ id: PROJECT, name: "x", updated_at: "2026-01-01", summary: { champion: champion && { metric_scope: "cv_aggregate", ...(champion as object) } } }) as Parameters<typeof championMetric>[0];
  assert.equal(championMetric(p(null)), null);
  assert.equal(championMetric(p({ version: "v1", cv_metrics: {} })), null);
  assert.deepEqual(championMetric(p({ version: "v1", selection_metric: "roc_auc", cv_metrics: { accuracy: 0.9, roc_auc: 0.8 } })), { name: "roc_auc", value: 0.8 });
  assert.equal(championMetric(p({ version: "v1", selection_metric: "missing", cv_metrics: { b: 2, a: 1 } }))!.name, "a");
});

test("runs in progress count the latest run of each project; recent sorts newest first", () => {
  const mk = (status: string | null, updated_at: string) => ({ id: PROJECT, name: "n", updated_at, summary: { latest_run: status ? { status } : null } });
  const list = [mk("running", "2026-01-02"), mk("completed", "2026-01-03"), mk("queued", "2026-01-01"), mk(null, "2026-01-04")];
  assert.equal(runsInProgress(list), 2);
  assert.deepEqual(recentProjects(list, 2).map((x) => x.updated_at), ["2026-01-04", "2026-01-03"]);
});

test("champion metric is shown only for a cross-validation aggregate", () => {
  const p = (metric_scope?: string) => ({ id: PROJECT, name: "x", updated_at: "2026", summary: { champion: { version: "v1", metric_scope, cv_metrics: { auc: 0.7 } } } }) as Parameters<typeof championMetric>[0];
  assert.deepEqual(championMetric(p("cv_aggregate")), { name: "auc", value: 0.7 });
  assert.equal(championMetric(p("holdout")), null);
  assert.equal(championMetric(p(undefined)), null);
});

test("scores are shown in the metric's own units: stored negated errors come back positive", () => {
  const p = (selection_metric: string, cv_metrics: Record<string, number>) => ({ id: PROJECT, name: "x", updated_at: "2026", summary: { champion: { version: "v1", metric_scope: "cv_aggregate", selection_metric, cv_metrics } } }) as Parameters<typeof championMetric>[0];
  assert.deepEqual(championMetric(p("rmse", { rmse: -2.5 })), { name: "rmse", value: 2.5 });
  assert.deepEqual(championMetric(p("rmse", { rmse: 2.5 })), { name: "rmse", value: 2.5 });
  assert.deepEqual(championMetric(p("r2", { r2: 0.4 })), { name: "r2", value: 0.4 });
  assert.equal(championScoreText(p("pr_auc", { pr_auc: 0.7049 })), "PR-AUC 0.70");
  assert.equal(championScoreText(p("mae", { mae: -120.55 })), "MAE 120.5");
  // A selection metric called like an inherited member falls back to the first recorded metric.
  assert.equal(championMetric(p("__proto__", { b: 2, a: 1 }))!.name, "a");
  assert.equal(championMetric(p("constructor", { z: 1 }))!.name, "z");
  assert.equal(championScoreText(p("pr_auc", { pr_auc: Number.NaN })), null);
});

test("activity reads as sentences with honest wording for every kind", () => {
  const row = (over: object) => ({ kind: "decision", project_id: PROJECT, link: { kind: "decision_record", id: RECORD }, summary: "", status: "accepted", decision_type: "champion_promoted", ...over }) as Parameters<typeof activitySentence>[0];
  assert.equal(activitySentence(row({}), null), "Put a model in use");
  assert.equal(activitySentence(row({ status: "proposed" }), null), "Waiting for your decision: Put this model in use");
  assert.equal(activitySentence(row({ status: "rejected", decision_type: "experiment_accepted" }), null), "Rejected: Accept this run");
  assert.match(activitySentence(row({ status: "superseded" }), null), /since corrected/);
  assert.equal(activitySentence(row({ decision_type: "__proto__" }), null), "Recorded: proto");
  assert.equal(activitySentence(row({ kind: "run_finished", summary: "Run #4 completed" }), "Run 4"), "Run 4 finished");
  assert.equal(activitySentence(row({ kind: "run_finished", summary: "Run #5 failed" }), null), "A run failed");
  assert.equal(activitySentence(row({ kind: "run_queued", summary: "Run #6 queued" }), "Run 6"), "Run 6 was queued");
  assert.equal(activitySentence(row({ kind: "run_finished", summary: "Run #6 something" }), "Run 6"), "Run 6 ended");
  assert.equal(activitySentence(row({ kind: "agent_run_finished", status: "completed" }), null), "An AI agent run finished");
  assert.equal(activitySentence(row({ kind: "final_test_run", summary: "Final test run once for Run #1" }), "Run 1"), "Run 1 was checked once on its final test set");
  assert.equal(isAssistantRow({ kind: "agent_run_started" }), true);
  assert.equal(isAssistantRow({ kind: "run_finished" }), false);
  // No internal words survive in any sentence.
  for (const kind of ["decision", "run_queued", "run_finished", "agent_run_started", "agent_run_finished"]) assert.doesNotMatch(activitySentence(row({ kind }), "Run 1"), /champion|ref |holdout|proposal|decision point/i);
});

test("live runs name the project and only count queued, running or stopping runs", () => {
  const E = "44444444-4444-4444-8444-444444444444";
  const mk = (status: string | null, experiment_id: string) => ({ id: PROJECT, name: "n", updated_at: "2026", summary: { latest_run: status ? { status, experiment_id } : null } });
  const rows = liveRuns([mk("running", E), mk("completed", E), mk("queued", "not-a-uuid"), mk(null, E)]);
  assert.deepEqual(rows.map((r) => [r.status, r.experimentId]), [["running", E], ["queued", null]]);
  // Other unfinished runs (an older run, or one waiting for an answer) are counted from running_count.
  const withCount = (status: string | null, running_count: number) => ({ id: PROJECT, name: "n", updated_at: "2026", summary: { running_count, latest_run: status ? { status, experiment_id: E } : null } });
  assert.deepEqual(liveRuns([withCount("running", 3), withCount("completed", 1), withCount("completed", 0)]).map((r) => [r.status, r.others]), [["running", 2], [null, 1]]);
  assert.equal(otherRunsText(2, true), "2 more runs are not finished yet");
  assert.equal(otherRunsText(1, false), "1 run is not finished yet");
});

test("greeting follows the hour", () => {
  assert.equal(greeting(8), "Good morning");
  assert.equal(greeting(12), "Good afternoon");
  assert.equal(greeting(19), "Good evening");
});

// --- V7 UI-B: Home v7 -------------------------------------------------------------------

const BANNED = /\b(release|champion|ref|digest|holdout|manifest|package|slot|tick|revision|window|psi)s?\b|final test/i;
const RUN = "55555555-5555-4555-8555-555555555555";

test("CV scores and finished-run results: cross-validation only, errors positive, families in words", () => {
  assert.equal(cvScoreText("pr_auc", 0.6812), "PR-AUC 0.68");
  assert.equal(cvScoreText("rmse", -2.5), "RMSE 2.50");
  assert.equal(cvScoreText(null, 0.5), null);
  assert.equal(cvScoreText("pr_auc", Number.POSITIVE_INFINITY), null);
  assert.equal(runResultText({ model_family: "catboost", metric: "pr_auc", cv_score: 0.68, metric_scope: "cv_aggregate" }), "CatBoost · CV PR-AUC 0.68");
  assert.equal(runResultText({ model_family: null, algorithm: "xgboost", metric: "pr_auc", cv_score: 0.7, metric_scope: "cv_aggregate" }), "XGBoost · CV PR-AUC 0.70");
  // Anything that is not a cross-validation aggregate is never shown as a score.
  assert.equal(runResultText({ model_family: "catboost", metric: "pr_auc", cv_score: 0.9, metric_scope: "final_holdout" }), null);
  assert.equal(runResultText({ model_family: "catboost", metric: "pr_auc", cv_score: 0.9 }), null);
  assert.equal(runResultText(null), null);
});

test("the title sentence leaves unknown counts out and never says 0 for unknown", () => {
  assert.equal(homeSubtitle(3, { kind: "all", runs: 1 }), "3 things need you. 1 run is not finished yet.");
  assert.equal(homeSubtitle(0, { kind: "all", runs: 0 }), "Nothing needs you right now. Nothing is running.");
  assert.equal(homeSubtitle(undefined, { kind: "all", runs: 2 }), "2 runs are not finished yet.");
  assert.equal(homeSubtitle(1, undefined), "1 thing needs you.");
  assert.equal(homeSubtitle(undefined, undefined), "");
  // Only the latest run of each project known: nothing is claimed about older runs.
  assert.equal(runningSentence({ kind: "latest", projects: 0 }), "No project's latest run is in progress.");
  assert.equal(runningSentence({ kind: "latest", projects: 2 }), "Runs are in progress in 2 projects.");
});

test("running count: all unfinished runs when every summary has running_count, else latest runs only", () => {
  const p = (running_count: number | undefined, status: string | null) => ({ id: PROJECT, name: "n", updated_at: "2026", summary: { running_count, latest_run: status ? { status } : null } });
  assert.deepEqual(runningCount([p(2, "running"), p(1, "completed"), p(0, null)]), { kind: "all", runs: 3 });
  assert.deepEqual(runningCount([p(2, "running"), p(undefined, "queued")]), { kind: "latest", projects: 2 });
  assert.deepEqual(runningCount([]), { kind: "all", runs: 0 });
});

const item = (over: object) => ({ kind: "decision_proposal", project_id: PROJECT, summary: "x", status: "proposed", can_act: true, proposed_by: "rule", decision_type: "experiment_accepted", subject: { kind: "experiment", id: RUN }, ...over }) as Parameters<typeof needsYouRow>[0];

test("what needs you: a title, one sentence from the item's fields and one button per kind", () => {
  const ctx = { projectName: "Churn", runName: "Run 3" };
  assert.deepEqual(needsYouRow(item({}), ctx), { title: "Accept this run?", sentence: "Suggested by the rules in Churn. Approve or reject it in the Inbox.", action: { label: "Decide", href: "/inbox" } });
  const viewer = needsYouRow(item({ can_act: false }), ctx);
  assert.deepEqual([viewer.action.label, viewer.sentence], ["Review", "Suggested by the rules in Churn. You can read it; someone with the right role decides."]);
  assert.deepEqual(needsYouRow(item({ kind: "question", summary: "Run needs a split confirmation" }), ctx).action, { label: "Answer", href: `/projects/${PROJECT}/experiments/${RUN}` });
  const drift = needsYouRow(item({ kind: "drift_alert", summary: "Drift alert on the model in use", subject: { kind: "monitoring_window", id: RUN } }), ctx);
  assert.deepEqual(drift.action, { label: "Open Monitoring", href: `/projects/${PROJECT}/monitoring` });
  assert.match(drift.sentence, /alert: drift, or real outcomes below a floor\. Monitoring says which\./);
  // D5c: the retrain rule's question and the retrained model both go to Monitoring.
  const ask = needsYouRow(item({ kind: "retrain_ask", status: "asked", summary: "SERVER" }), ctx);
  assert.deepEqual([ask.title, ask.action.href], ["Retrain on newest data?", `/projects/${PROJECT}/monitoring`]);
  assert.match(ask.sentence, /newer labelled file exists in Churn/);
  const ready = needsYouRow(item({ kind: "challenger_ready", status: "proposed", summary: "SERVER" }), ctx);
  assert.deepEqual([ready.title, ready.action.label], ["Retrained model ready to compare", "Open Monitoring"]);
  assert.match(ready.sentence, /Switch to it or keep the model in use/);
  assert.deepEqual(needsYouRow(item({ kind: "run_finished", summary: "Run #3 completed" }), ctx).action, { label: "Open", href: `/projects/${PROJECT}/experiments/${RUN}` });
  assert.equal(needsYouRow(item({ kind: "run_finished", summary: "Run #3 completed" }), ctx).title, "Run 3 finished");
  // No project name known: the sentence just leaves it out; bad ids never become links.
  assert.equal(needsYouRow(item({}), { projectName: null, runName: null }).sentence, "Suggested by the rules. Approve or reject it in the Inbox.");
  assert.equal(needsYouRow(item({ kind: "drift_alert", project_id: "../x" }), ctx).action.href, "/inbox");
  assert.equal(needsYouRow(item({ kind: "run_finished", subject: { kind: "experiment", id: "javascript:1" } }), ctx).action.href, "/inbox");
  assert.equal(needsYouRow(item({ kind: "__proto__", summary: "Something" }), ctx).sentence, "Waiting in the Inbox in Churn.");
});

test("running now: fold k of n only when both are recorded; started-ago words; nothing estimated", () => {
  const now = new Date("2026-10-16T10:00:00Z");
  assert.equal(startedAgo("2026-10-16T09:56:00Z", now), "started 4 minutes ago");
  assert.equal(startedAgo("2026-10-16T09:59:40Z", now), "started just now");
  assert.equal(startedAgo("2026-10-16T08:00:00Z", now), "started 2 hours ago");
  assert.equal(startedAgo("2026-10-14T08:00:00Z", now), "started Wed, 14 Oct 2026, 08:00 UTC");
  assert.equal(startedAgo("nope", now), "start time unknown");
  // Fold k is per model: in progress means k - 1 done; a latest cv_fold_completed means fold k is done.
  assert.deepEqual(foldMeter({ fold_number: 3, folds_planned: 5 }), { value: 2, max: 5, step: "Model being trained: fold 3 of 5", text: "Fold 3 of 5 in progress, 2 done" });
  assert.deepEqual(foldMeter({ fold_number: 3, folds_planned: 5, fold_completed: true }), { value: 3, max: 5, step: "Model being trained: fold 3 of 5 done", text: "Fold 3 of 5 done" });
  for (const bad of [{ fold_number: 6, folds_planned: 5 }, { fold_number: 0, folds_planned: 5 }, { fold_number: 2, folds_planned: null }, { fold_number: 1.5, folds_planned: 5 }, null]) assert.equal(foldMeter(bad), null);
  // Fold events carry no model family: the title names the run only.
  const run = { status: "running", created_at: "2026-10-16T09:56:00Z", run_number: 6, progress: { stage: "cross_validation", model_family: null, fold_number: 3, folds_planned: 5, candidates_finished: 2 } };
  assert.deepEqual(liveRunText(run, now), { title: "Run 6", detail: "Model being trained: fold 3 of 5 · 2 models finished · started 4 minutes ago" });
  assert.equal(liveRunText({ ...run, progress: { ...run.progress, model_family: "catboost", fold_number: null } }, null).title, "Run 6 · CatBoost");
  assert.deepEqual(liveRunText({ ...run, progress: null, status: "queued", run_number: null }, now), { title: "Latest run", detail: "Waiting to start · started 4 minutes ago" });
  assert.equal(liveRunText({ ...run, progress: { stage: "final_refit", candidates_finished: 0 } }, null).detail, "Refitting the chosen model");
  assert.equal(liveRunText({ ...run, progress: { stage: "__proto__" } }, null).detail, "proto");
  assert.equal(liveRunText({ ...run, status: "cancelling", progress: null }, null).detail, "Stopping");
});

test("live runs carry the run itself (number and progress) for the Running now rows", () => {
  const E = "44444444-4444-4444-8444-444444444444";
  const rows = liveRuns([{ id: PROJECT, name: "n", updated_at: "2026", summary: { latest_run: { status: "running", experiment_id: E, created_at: "2026", run_number: 2 } } }]);
  assert.equal(rows[0].run?.run_number, 2);
});

test("banned words: no Home sentence, title or button uses internal or final-test words", () => {
  const ctx = { projectName: "P", runName: "Run 1" };
  const sentences: string[] = [homeSubtitle(2, { kind: "all", runs: 3 }), homeSubtitle(0, { kind: "all", runs: 0 }), runningSentence({ kind: "latest", projects: 0 }), runningSentence({ kind: "latest", projects: 3 }), otherRunsText(2, true)];
  for (const fold_completed of [true, false]) sentences.push(...Object.values(foldMeter({ fold_number: 2, folds_planned: 5, fold_completed })!).filter((v): v is string => typeof v === "string"));
  for (const kind of ["decision_proposal", "agent_proposal", "question", "drift_alert", "run_finished", "other"]) {
    for (const can_act of [true, false]) {
      const row = needsYouRow(item({ kind, can_act, summary: kind === "run_finished" ? "Run #1 completed" : "Run needs a target column" }), ctx);
      sentences.push(row.sentence, row.action.label);
    }
  }
  const now = new Date("2026-10-16T10:00:00Z");
  for (const stage of ["cross_validation", "candidate_generation", "preprocessing", "final_refit", "deterministic_verification", null]) {
    for (const status of ["queued", "running", "cancelling"]) {
      const text = liveRunText({ status, created_at: "2026-10-16T09:00:00Z", run_number: 1, progress: stage ? { stage, candidates_finished: 1 } : null }, now);
      sentences.push(text.title, text.detail);
    }
  }
  sentences.push(runResultText({ model_family: "lightgbm", metric: "roc_auc", cv_score: 0.8, metric_scope: "cv_aggregate" })!);
  for (const s of sentences) assert.doesNotMatch(s, BANNED, s);
});
