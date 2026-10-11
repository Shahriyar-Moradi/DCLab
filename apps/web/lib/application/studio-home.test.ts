import assert from "node:assert/strict";
import test from "node:test";
import { activitySentence, actorLabel, activityHref, aiIsOff, championMetric, championScoreText, greeting, isAssistantRow, liveRuns, recentProjects, runsInProgress } from "./studio-home.ts";

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
});

test("greeting follows the hour", () => {
  assert.equal(greeting(8), "Good morning");
  assert.equal(greeting(12), "Good afternoon");
  assert.equal(greeting(19), "Good evening");
});
