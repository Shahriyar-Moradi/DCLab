import assert from "node:assert/strict";
import test from "node:test";
import {
  actorWords, constraintText, designChips, designKind, goalCards, goalFacts, newestSplitPlan, planDesign, problemSpecOf, metricInfo, ruleWords, taskLabel,
  timeline, timelineSummary, whyFromRows, type SplitPlanLike,
} from "./studio-goal.ts";

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

const plan = (over: Partial<SplitPlanLike> = {}): SplitPlanLike => ({
  row_count: 48210, train_row_count: 39306, evaluation_row_count: 8904, has_evaluation_set: true, evaluation_strategy: "temporal_future",
  validation_strategy: "TimeSeriesSplit", validation_folds: 5, stratified: false, time_column: "snapshot_month", group_column: null, folds_status: "computed",
  folds: [5, 4, 3, 2, 1].map((n) => ({ fold_number: n, train_row_count: 6551 * n, validation_row_count: 6551 })),
  ...over,
});
const BANNED = /\b(holdout|hold-out|champion|digest|refs?|stale|releases?|manifest|package|slots?|ticks?|revision|lineage|candidates?)\b/i;

test("planDesign reads the split plan read: folds in order, row counts, share; nothing from the final test rows but their count", () => {
  const d = planDesign(plan());
  assert.deepEqual([d.kind, d.testKind, d.folds, d.trainRows, d.testRows, d.totalRows], ["time", "time", 5, 39306, 8904, 48210]);
  assert.deepEqual(d.perFold.map((f) => f.fold), [1, 2, 3, 4, 5]);
  assert.ok(Math.abs((d.testShare ?? 0) - 8904 / 48210) < 1e-9);
  assert.deepEqual(planDesign(plan({ folds_status: "unavailable" })).perFold, []);
  const none = planDesign(plan({ has_evaluation_set: false, evaluation_row_count: 0 }));
  assert.deepEqual([none.hasTest, none.testShare], [false, null]);
  // Malformed counts are dropped, never guessed.
  assert.equal(planDesign(plan({ folds: [{ fold_number: 1, train_row_count: -3, validation_row_count: 10 }] })).perFold.length, 0);
});

test("timeline is to scale: time folds learn from the start and check right after; others place one check block per fold; all in 0..1", () => {
  const t = timeline(planDesign(plan()));
  assert.ok(t);
  assert.ok(Math.abs(t.trainEnd - 39306 / 48210) < 1e-9);
  assert.deepEqual(t.rows[0].learn, [{ x: 0, w: 6551 / 48210 }]);
  assert.ok(Math.abs(t.rows[0].check.x - 6551 / 48210) < 1e-9);
  assert.ok(Math.abs(t.rows[4].check.x + t.rows[4].check.w - t.trainEnd) < 1e-9);
  assert.ok(t.test && Math.abs(t.test.w - 8904 / 48210) < 1e-9 && Math.abs(t.test.x - t.trainEnd) < 1e-9);
  // The final test bar is its own row share, even when the plan's total has rows outside both parts.
  const wide = timeline(planDesign(plan({ row_count: 50000 })));
  assert.ok(wide?.test && Math.abs(wide.test.w - 8904 / 50000) < 1e-9);
  const random = timeline(planDesign(plan({ validation_strategy: "StratifiedKFold", evaluation_strategy: "stratified_random", folds: [1, 2, 3].map((n) => ({ fold_number: n, train_row_count: 26204, validation_row_count: 13102 })) })));
  assert.ok(random);
  assert.equal(random.rows[0].learn.length, 1);
  assert.equal(random.rows[1].learn.length, 2);
  for (const row of random.rows) for (const seg of [...row.learn, row.check]) assert.ok(seg.x >= 0 && seg.x + seg.w <= random.trainEnd + 1e-9);
  const many = timeline(planDesign(plan({ folds: Array.from({ length: 12 }, (_, i) => ({ fold_number: i + 1, train_row_count: 100 * (i + 1), validation_row_count: 100 })) })));
  assert.deepEqual([many?.rows.length, many?.capped], [10, true]);
  assert.equal(timeline(planDesign(plan({ row_count: 0, train_row_count: 0, evaluation_row_count: 0 }))), null);
  assert.equal(timeline(planDesign(plan({ has_evaluation_set: false, evaluation_row_count: 0 })))?.test, null);
});

test("chips and the why sentence come from row counts only; no dates; the two plans are named when they differ", () => {
  const d = planDesign(plan());
  const chips = designChips(d);
  assert.equal(chips[0].title, "Training rows: 39,306");
  assert.equal(chips[0].detail, "5 time-ordered cross-validation folds");
  assert.equal(chips[1].title, "Final test set: 8,904 rows (18.5%)");
  assert.match(chips[1].detail, /latest rows in time · used once per run/);
  const why = whyFromRows(d, (v) => v.toUpperCase()) ?? "";
  assert.match(why, /Time-ordered by SNAPSHOT_MONTH\. Fold 1 learns from the first 6,551 rows and is checked on the next 6,551; .* up to 32,755 rows in fold 5\. The final test set is the latest 8,904 rows \(18\.5%\)/);
  assert.doesNotMatch(why, /20\d\d|Jan|Feb|Mar/);
  assert.match(whyFromRows(planDesign(plan({ validation_strategy: "GroupKFold", evaluation_strategy: "group_disjoint", group_column: "customer" }))) ?? "", /Grouped by customer: .*about 6,551 rows from groups it did not learn from/);
  assert.match(whyFromRows(planDesign(plan({ evaluation_strategy: "group_disjoint" }))) ?? "", /folds are time-ordered and the final test set is grouped/);
  assert.equal(whyFromRows(planDesign(plan({ validation_strategy: "unknown", evaluation_strategy: "unknown" }))), null);
  assert.equal(designChips(planDesign(plan({ has_evaluation_set: false, evaluation_row_count: 0 })))[1].title, "No final test set");
  assert.match(timelineSummary(d), /^Test design drawn to scale by rows: 5 time-ordered cross-validation folds on 39,306 training rows \(fold 1 learns from 6,551 rows/);
  assert.equal(designKind("group_disjoint", "time_series_split"), "group");
  assert.equal(designKind(null, "stratified_kfold"), "random");
  assert.equal(designKind("unsupported"), null);
});

test("goalCards: big sentences from the problem spec, the business rule in words, the ranking reason given, from the data, or general", () => {
  const spec = {
    target_column: "churned_next_30d", task_type: "binary", primary_metric: "pr_auc", prediction_unit: "customer-month", prediction_horizon: "30 days",
    business_objective: "Who will cancel in the next 30 days?", constraints: { metric_constraints: [{ metric: "recall", op: ">=", value: 0.8 }, { metric: "bogus", op: "==", value: 1 }] },
  };
  const cards = goalCards(spec, goalFacts(undefined, null), { targetRate: 0.213 });
  assert.equal(cards.target, "churned_next_30d");
  assert.equal(cards.metric?.label, "PR-AUC");
  assert.equal(cards.metricWhy, "Only about 1 in 5 training rows has the answer the model treats as yes, and PR-AUC rewards finding them without flagging many others.");
  assert.deepEqual(cards.rules.map((r) => r.big), ["Recall ≥ 0.80"]);
  assert.equal(cards.rules[0].meaning, "Catch at least 80% of the rows that really are positive.");
  assert.equal(cards.unitLine, "One answer per customer-month. Prediction horizon: 30 days.");
  assert.equal(cards.question, "Who will cancel in the next 30 days?");
  // A reason given with the goal wins; it passes through the cleaner.
  const given = goalCards({ ...spec, constraints: { primary_metric_reason: "rare <b>churn</b>" } }, goalFacts(undefined, null), { clean: (v) => v.replace(/[<>]/g, "") });
  assert.equal(given.metricWhy, "Reason given: rare bchurn/b");
  assert.equal(given.rules.length, 0);
  assert.equal(given.rulesKnown, true);
  // Without a spec: the run's facts fill the target and metric; the rule is "not recorded", never "none".
  const fallback = goalCards(null, { target: "y", task: null, metric: metricInfo("rmse"), businessRule: null, ruleNotRequested: false });
  assert.deepEqual([fallback.target, fallback.metric?.label, fallback.rulesKnown], ["y", "RMSE", false]);
  assert.equal(goalCards({ ...spec, primary_metric: "roc_auc" }, goalFacts(undefined, null), { targetRate: 0.2 }).metricWhy, metricInfo("roc_auc")?.about);
  assert.equal(ruleWords({ metric: "precision", op: ">=", value: 0.6 })?.meaning, "When the model flags a row, it is right at least 60% of the time.");
  assert.equal(ruleWords({ metric: "mae", op: "<=", value: 12.5 })?.big, "MAE ≤ 12.50");
  assert.equal(ruleWords({ metric: "__proto__", op: ">=", value: 0.5 })?.big, "proto ≥ 0.50"); // a plain label, never a prototype lookup
  assert.equal(ruleWords(null), null);
  const cost = goalCards({ ...spec, constraints: { cost_matrix: { false_positive: 1, false_negative: 5 } } }, goalFacts(undefined, null));
  assert.equal(cost.rules[0].meaning, "A missed positive costs 5; a false alarm costs 1.");
  for (const text of [cards.metricWhy, ...cards.rules.map((r) => r.meaning), ...designChips(planDesign(plan())).flatMap((c) => [c.title, c.detail]), whyFromRows(planDesign(plan())), timelineSummary(planDesign(plan()))]) {
    assert.doesNotMatch(text ?? "", BANNED, text ?? "");
  }
});

test("newestSplitPlan: the test design in use, else the newest one in the graph; never a non-UUID", () => {
  const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const nodes = [{ kind: "split_plan", id: A, created_at: "2026-01-01" }, { kind: "split_plan", id: B, created_at: "2026-02-01" }, { kind: "split_plan", id: "../x", created_at: "2027-01-01" }, { kind: "experiment", id: A }];
  assert.equal(newestSplitPlan(A, nodes), A);
  assert.equal(newestSplitPlan(null, nodes), B);
  assert.equal(newestSplitPlan("not-a-uuid", []), null);
  assert.equal(newestSplitPlan(undefined, undefined), null);
});

test("actorWords and problemSpecOf", () => {
  assert.equal(actorWords({ kind: "agent", agent_run_id: "r" }), "the assistant");
  assert.equal(actorWords({ kind: "agent", service_token_id: "t" }), "a connected tool (access token)");
  assert.equal(actorWords({ kind: "agent" }), "an agent");
  assert.equal(actorWords({ kind: "__proto__" }), "someone else");
  assert.equal(actorWords({ kind: "constructor" }), "someone else");
  assert.equal(problemSpecOf([{ from: { kind: "experiment", id: "E" }, to: { kind: "problem_spec", id: "P1" }, relation: "uses_problem_spec" }], "E"), "P1");
});
