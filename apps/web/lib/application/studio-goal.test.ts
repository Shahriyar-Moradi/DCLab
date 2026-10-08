import assert from "node:assert/strict";
import test from "node:test";
import { actorWords, constraintText, decidedRows, designGraphic, designKind, goalFacts, problemSpecOf, metricInfo, taskLabel, testDesign, whyText } from "./studio-goal.ts";

const stage = (key: string, configuration: Record<string, unknown>) => ({ key, configuration });
const build = {
  stages: [
    stage("target_task", { target_column: "churned", task_type: "binary" }),
    stage("metric_plan", { primary_metric: "pr_auc" }),
    stage("final_holdout_plan", { strategy: "temporal_future", test_size: 0.2, time_column: "month" }),
    stage("holdout_lock", { locked: true, locked_at: "2026-06-01T00:00:00Z" }),
    stage("validation_plan", { strategy: "time_series_split", actual_folds: 5, requested_folds: 5 }),
  ],
};

test("goalFacts reads the target, kind of answer and ranking metric from the run and omits what is missing", () => {
  const facts = goalFacts(build, { metrics: { constraint_status: "satisfied" } });
  assert.equal(facts.target, "churned");
  assert.equal(facts.task, "A yes/no answer (binary classification)");
  assert.equal(facts.metric?.label, "PR-AUC");
  assert.match(facts.businessRule ?? "", /met the business rule/);
  const empty = goalFacts(undefined, null);
  assert.deepEqual([empty.target, empty.task, empty.metric, empty.businessRule], [null, null, null, null]);
  // Falls back to the run detail when the stages are missing.
  assert.equal(goalFacts(undefined, { target_column: "y", task_type: "regression", metrics: { selection_metric: "rmse" } }).metric?.label, "RMSE");
  assert.equal(goalFacts(undefined, { metrics: { constraint_status: "not_requested" } }).ruleNotRequested, true);
  assert.equal(constraintText("not_requested"), null);
  assert.match(constraintText("unsatisfiable") ?? "", /The selected model could not meet/);
});

test("metricInfo and taskLabel never invent a reason for an unknown name", () => {
  assert.deepEqual(metricInfo("custom_score"), { label: "custom score", about: null });
  assert.equal(metricInfo(""), null);
  assert.equal(metricInfo("__proto__")?.about, null);
  assert.equal(taskLabel("regression"), "A number (regression)");
  assert.equal(taskLabel(null), null);
});

test("testDesign takes the strategy, folds and final test share from the recorded plan and card", () => {
  const d = testDesign(build, { train_rows: 39306, evaluation_rows: 8904 });
  assert.equal(d.kind, "time");
  assert.equal(d.folds, 5);
  assert.equal(d.timeColumn, "month");
  assert.equal(d.locked, true);
  assert.ok(Math.abs((d.testShare ?? 0) - 8904 / 48210) < 1e-9);
  // Without a card the planned fraction is used; without either there is no share.
  assert.equal(testDesign(build, null).testShare, 0.2);
  assert.equal(testDesign({ stages: [] }, null).testShare, null);
  assert.equal(testDesign({ stages: [] }, null).kind, null);
});

test("designKind reads the first known strategy; folds and the final test set are kept apart", () => {
  assert.equal(designKind("group_disjoint", "time_series_split"), "group");
  const mixed = testDesign({ stages: [stage("final_holdout_plan", { strategy: "group_disjoint" }), stage("validation_plan", { strategy: "time_series_split", actual_folds: 4 })] }, null);
  assert.deepEqual([mixed.kind, mixed.testKind], ["time", "group"]);
  assert.match(whyText(mixed) ?? "", /folds are time-ordered and the final test set is grouped/);
  assert.equal(designKind(null, "stratified_kfold"), "random");
  assert.equal(designKind("unsupported"), null);
});

test("whyText follows the strategy and passes column names through the cleaner", () => {
  const d = testDesign(build, null);
  assert.match(whyText(d, (v) => v.toUpperCase()) ?? "", /Time-ordered by MONTH/);
  assert.match(whyText({ ...d, kind: "group", testKind: "group", groupColumn: "customer" }) ?? "", /Grouped by customer/);
  assert.equal(whyText({ ...d, kind: null, testKind: null }), null);
});

test("designGraphic: time folds grow and validate on the next block; others hold one block out; widths stay in 0..100", () => {
  const time = designGraphic("time", 5, 0.2);
  assert.ok(time);
  assert.equal(time.folds.length, 5);
  assert.equal(time.trainWidth, 80);
  assert.equal(time.folds[0].train[0].x, 0);
  assert.ok(time.folds[4].train[0].w < time.trainWidth);
  assert.ok(Math.abs(time.folds[4].validation.x + time.folds[4].validation.w - time.trainWidth) < 1e-9);
  const random = designGraphic("random", 4, null);
  assert.ok(random);
  assert.equal(random.folds[0].train.length, 1);
  assert.equal(random.folds[1].train.length, 2);
  for (const bar of random.folds) for (const r of [...bar.train, bar.validation]) assert.ok(r.x >= 0 && r.x + r.w <= 100 + 1e-9);
  assert.equal(designGraphic("time", 25, 0.2)?.folds.length, 10);
  assert.equal(designGraphic("time", 25, 0.2)?.capped, true);
  assert.equal(designGraphic(null, 5, 0.2), null);
  assert.equal(designGraphic("time", null, 0.2), null);
  assert.equal(designGraphic("time", 1, 0.2), null);
});

test("decidedRows states only who recorded the decision; a missing record gives no row", () => {
  const items = [
    { id: "a", decision_type: "problem_spec_locked", effective_state: "accepted", recorded_at: "2026-06-01T00:00:00Z", actor: { kind: "rule" }, subject: { kind: "problem_spec", id: "P1" } },
    { id: "a2", decision_type: "problem_spec_locked", effective_state: "accepted", recorded_at: "2026-07-01T00:00:00Z", actor: { kind: "human" }, subject: { kind: "problem_spec", id: "P2" } },
    { id: "b", decision_type: "split_plan_created", effective_state: "accepted", recorded_at: "2026-06-02T00:00:00Z", actor: { kind: "agent", service_token_id: "t" }, subject: { kind: "split_plan", id: "S1" } },
    { id: "c", decision_type: "split_plan_created", effective_state: "accepted", recorded_at: "2026-06-03T00:00:00Z", actor: { kind: "human" }, subject: { kind: "split_plan", id: "OTHER" } },
  ];
  const rows = decidedRows(items, "S1", "P1");
  assert.deepEqual(rows.map((r) => r.by), ["the rules", "a connected tool (access token)"]);
  assert.equal(JSON.stringify(rows).includes("assistant"), false);
  assert.deepEqual(decidedRows(items, "S1", null).map((r) => r.key), ["b"]);
  assert.deepEqual(decidedRows([], "S1", "P1"), []);
  assert.equal(actorWords({ kind: "agent", agent_run_id: "r" }), "the assistant");
  assert.equal(actorWords({ kind: "agent" }), "an agent");
  assert.equal(actorWords({ kind: "__proto__" }), "someone else");
  assert.equal(actorWords({ kind: "constructor" }), "someone else");
  assert.equal(problemSpecOf([{ from: { kind: "experiment", id: "E" }, to: { kind: "problem_spec", id: "P1" }, relation: "uses_problem_spec" }], "E"), "P1");
});
