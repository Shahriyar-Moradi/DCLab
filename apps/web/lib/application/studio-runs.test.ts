import assert from "node:assert/strict";
import test from "node:test";
import {
  anyAiAnswer, beatsText, bestRunIds, changeSentences, familyLabel, selectionScore, matchesStatusFilter, pointLabel, runFacts, runSteps, scoreText,
  stageStateWords, stageWords, statusWords, trustCounts, trustSentence,
} from "./studio-runs.ts";

test("model families are words; unknown and prototype keys are shown as text, not looked up", () => {
  assert.equal(familyLabel("lightgbm"), "LightGBM");
  assert.equal(familyLabel("majority"), "Baseline (most common answer)");
  assert.equal(familyLabel("weird_new_model"), "weird new model");
  for (const key of ["__proto__", "constructor", "toString", "hasOwnProperty"]) assert.equal(typeof familyLabel(key), "string");
  assert.equal(familyLabel("constructor"), "constructor");
  assert.equal(familyLabel(""), null);
  assert.equal(familyLabel(null), null);
});

test("change sets read as sentences from the real kinds; untrusted values are cleaned and capped", () => {
  assert.deepEqual(changeSentences({ changes: [{ kind: "class_weighting", mode: "balanced" }] }), ["Balanced class weights"]);
  assert.deepEqual(changeSentences({ changes: [{ kind: "family_exclude", family: "catboost" }, { kind: "family_include", family: "lightgbm" }] }), ["Dropped the CatBoost model family", "Added the LightGBM model family"]);
  assert.deepEqual(changeSentences({ changes: [{ kind: "feature_transform_add", column: "customer_id", transform: "drop_column" }] }), ["Column customer_id: leave it out"]);
  assert.deepEqual(changeSentences({ changes: [{ kind: "hyperparameter_override", family: "lightgbm", parameters: { n_estimators: 500 } }] }), ["Fixed LightGBM settings: n_estimators = 500"]);
  assert.match(changeSentences({ changes: [{ kind: "metric_override", primary_metric: "pr_auc", reason: "rare outcome" }] })[0], /^Ranked models on PR-AUC \(rare outcome\)$/);
  assert.equal(changeSentences({ changes: [{ kind: "something_new" }] })[0], "something new");
  assert.equal(changeSentences({ changes: [{ kind: "__proto__" }] })[0], "proto");
  assert.equal(changeSentences({ changes: [{ kind: "feature_transform_add", column: "x".repeat(500), transform: "constructor" }] })[0].length < 140, true);
  assert.deepEqual(changeSentences({ changes: [{ kind: "threshold_objective", constraints: [{ metric: "recall", op: ">=", value: 0.8 }], cost_false_positive: 1, cost_false_negative: 5 }] }),
    ["Changed the threshold rule (rule: recall >= 0.8; a wrong alarm costs 1, a miss costs 5)"]);
  assert.deepEqual(changeSentences({ changes: [{ kind: "class_weighting", mode: "custom", weights: { yes: 3, no: 1 } }] }), ["Custom class weights: yes = 3, no = 1"]);
  const many = Object.fromEntries(Array.from({ length: 12 }, (_, i) => [`p${i}`, i]));
  assert.match(changeSentences({ changes: [{ kind: "hyperparameter_override", family: "lightgbm", parameters: many }] })[0], /p11 = 11$/);
  assert.match(changeSentences({ changes: [{ kind: "class_weighting", mode: "custom", weights: many }] })[0], /and 4 more$/);
  assert.doesNotMatch(changeSentences({ changes: [{ kind: "hyperparameter_override", family: "lightgbm", parameters: { a: { b: 1 } } }] })[0], /object Object/);
  assert.deepEqual(changeSentences(null), []);
  assert.deepEqual(changeSentences({ changes: "nope" }), []);
});

const detail = (m: Record<string, unknown>) => ({ metrics: { family: "lightgbm", selection_metric: "pr_auc", selected_score: 0.7, ...m } });

test("score, spread and baseline come from the run's own cross-validation numbers", () => {
  const f = runFacts(detail({ baseline_comparison: { metric: "pr_auc", beats_baseline: true, margin: 0.48, winner_cv_std: 0.02, clear_margin: true, winner_cv_score: 0.7 } }));
  assert.equal(scoreText(f), "0.70 ± 0.02");
  assert.equal(beatsText(f), "Yes, +0.48");
  assert.equal(f.metric, "PR-AUC");
  const small = runFacts(detail({ baseline_comparison: { metric: "pr_auc", beats_baseline: true, margin: 0.01, winner_cv_std: 0.03, clear_margin: false } }));
  assert.equal(beatsText(small), "Yes, +0.01, but within the spread");
  assert.equal(beatsText(runFacts(detail({ baseline_comparison: { metric: "pr_auc", beats_baseline: false, margin: -0.1 } }))), "No");
  // No baseline recorded is not the same as "no".
  assert.equal(beatsText(runFacts(detail({ baseline_comparison: null }))), "Not recorded");
  // A baseline measured on another metric says nothing about this score.
  const other = runFacts(detail({ baseline_comparison: { metric: "roc_auc", beats_baseline: true, margin: 0.2, winner_cv_std: 0.01 } }));
  assert.equal(other.spread, null);
  assert.equal(beatsText(other), "Not recorded");
  assert.equal(scoreText(other), "0.70");
  assert.equal(scoreText(runFacts(null)), null);
  assert.equal(beatsText(runFacts(detail({ family: "majority", baseline_comparison: null }))), "This is the baseline");
});

test("trust counts keep not checked apart from passed", () => {
  assert.equal(trustCounts(undefined).state, "pending");
  assert.equal(trustCounts({ investigated: false, checks: [] }).state, "not_checked");
  assert.equal(trustCounts({ investigated: true, checks: [] }).state, "not_checked");
  const c = trustCounts({ investigated: true, checks: [{ status: "pass" }, { status: "pass" }, { status: "warning" }, { status: "fail" }, { status: "not_evaluated" }] });
  assert.deepEqual(c, { state: "ready", pass: 2, warn: 1, fail: 1, notChecked: 1, total: 5 });
  assert.equal(trustSentence(c), "2 passed · 1 to review · 1 failed · 1 not checked");
  assert.equal(trustSentence(trustCounts({ investigated: false })), "Not checked yet");
});

const row = (id: string, score: number | null, extra: Record<string, unknown> = {}) => ({ id, completed: true, splitPlanId: "p1", metricKey: "pr_auc", score, baseline: false, ...extra });
const ids = (r: { ids: Set<string> }) => [...r.ids].sort();

test("best on cross-validation only among comparable scored runs; never the baseline; never a lone run", () => {
  assert.deepEqual(ids(bestRunIds([row("a", 0.66), row("b", 0.7), row("c", 0.68)])), ["b"]);
  assert.deepEqual(ids(bestRunIds([row("a", 0.7), row("b", 0.7)])), ["a", "b"]);
  assert.deepEqual(ids(bestRunIds([row("a", 0.22, { baseline: true }), row("b", 0.7), row("c", 0.6)])), ["b"]);
  assert.equal(bestRunIds([row("a", 0.7)]).ids.size, 0);
  assert.equal(bestRunIds([row("a", 0.7), row("b", 0.8, { splitPlanId: "p2" })]).ids.size, 0);
  assert.equal(bestRunIds([row("a", 0.7), row("b", 0.8, { metricKey: "roc_auc" })]).ids.size, 0);
  assert.equal(bestRunIds([row("a", 0.7), row("b", null)]).ids.size, 0);
  // Runs without a recorded test design are never treated as comparable (null is not "the same design").
  assert.equal(bestRunIds([row("a", 0.7, { splitPlanId: null }), row("b", 0.8, { splitPlanId: null })]).ids.size, 0);
  // Error scores: the LOWER number is best.
  assert.deepEqual(ids(bestRunIds([row("a", 3, { metricKey: "rmse" }), row("b", 2, { metricKey: "rmse" })])), ["b"]);
  assert.deepEqual(ids(bestRunIds([row("a", 0.9, { completed: false }), row("b", 0.7), row("c", 0.6)])), ["b"]);
  // A lead smaller than the best run's own spread is flagged.
  assert.equal(bestRunIds([row("a", 0.70, { spread: 0.05 }), row("b", 0.68)]).withinSpread, true);
  assert.equal(bestRunIds([row("a", 0.80, { spread: 0.02 }), row("b", 0.68)]).withinSpread, false);
});

test("error scores: the API stores them negated; the shown score and the ranking use natural units", () => {
  // RMSE 2.0 is stored as -2.0 (larger is better); the cv mean keeps natural units.
  const a = runFacts({ metrics: { family: "lightgbm", selection_metric: "rmse", selected_score: -2.0, cv: { rmse: 2.0 }, baseline_comparison: { metric: "rmse", beats_baseline: true, margin: 1.5, winner_cv_std: 0.1, winner_cv_score: -2.0 } } });
  const b = runFacts({ metrics: { family: "catboost", selection_metric: "rmse", selected_score: -3.0, baseline_comparison: { metric: "rmse", beats_baseline: true, margin: 0.5, winner_cv_std: 0.1, winner_cv_score: -3.0 } } });
  assert.equal(scoreText(a), "2.00 ± 0.10");
  assert.equal(scoreText(b), "3.00 ± 0.10"); // no cv map: flipped back
  const best = bestRunIds([{ id: "a", completed: true, splitPlanId: "p", metricKey: "rmse", score: a.score, baseline: false }, { id: "b", completed: true, splitPlanId: "p", metricKey: "rmse", score: b.score, baseline: false }]);
  assert.deepEqual(ids(best), ["a"]);
  assert.equal(selectionScore("rmse", null, -2.5), 2.5);
  assert.equal(selectionScore("pr_auc", null, 0.7), 0.7);
  assert.equal(selectionScore("accuracy", { accuracy: 0.9 }, 0.9), 0.9);
  assert.equal(selectionScore("pr_auc", null, null), null);
});

test("steps group the recorded stages, add up their times and leave out what was not recorded", () => {
  const order = runSteps([
    { key: "ingestion", status: "completed", duration_ms: 1 }, { key: "profiling_eda", status: "completed", duration_ms: 1 }, { key: "target_task", status: "completed", duration_ms: 1 },
    { key: "final_holdout_plan", status: "completed" }, { key: "holdout_lock", status: "completed" }, { key: "problem_profile", status: "completed" }, { key: "leakage_audit", status: "completed" },
  ]).map((x) => x.id);
  // The final test set is set aside before anything is profiled, and ingestion is part of "read".
  assert.deepEqual(order, ["read", "split", "profile"]);
  assert.equal(runSteps([{ key: "holdout_lock", status: "skipped" }])[0].state, "skipped");
  assert.equal(runSteps([{ key: "holdout_lock", status: "cancelled" }])[0].state, "stopped");
  assert.equal(runSteps([{ key: "cv_training", status: "completed" }, { key: "candidate_generation", status: "pending" }])[0].state, "partly");
  const steps = runSteps([
    { key: "feature_engineering", status: "completed", duration_ms: 46_000 }, { key: "preprocessing", status: "completed", duration_ms: 4_000 },
    { key: "cv_training", status: "running", duration_ms: null }, { key: "candidate_generation", status: "completed", duration_ms: 1000 },
    { key: "final_holdout", status: "pending" }, { key: "brand_new_stage", status: "failed", duration_ms: 5 },
  ]);
  assert.deepEqual(steps.map((s) => [s.id, s.state, s.ms]), [["features", "done", 50_000], ["train", "running", 1000], ["final", "waiting", null], ["other", "failed", 5]]);
  assert.equal(steps[0].time, "50 s");
  assert.equal(steps[2].time, "—");
  assert.deepEqual(runSteps([]), []);
  assert.equal(runSteps([{ key: "__proto__", status: "completed" }])[0].id, "other");
});

test("step, status and choice names are words and hold up against prototype keys", () => {
  assert.equal(stageWords("winner_lock", "Winner lock"), "Choose the best model");
  assert.equal(stageWords("unknown_stage", "Some  engine stage"), "Some engine stage");
  assert.equal(stageWords("constructor", "Odd title"), "Odd title");
  assert.equal(stageStateWords("completed"), "done");
  assert.equal(stageStateWords("failed"), "failed");
  assert.equal(statusWords("needs_input"), "Waiting for your input");
  assert.equal(statusWords("__proto__"), "proto");
  assert.equal(pointLabel("target.column"), "Which column to predict");
  assert.equal(pointLabel("new.kind_of_point"), "new kind of point");
  assert.equal(pointLabel("constructor"), "constructor");
  assert.equal(matchesStatusFilter("needs_input", "live"), true);
  assert.equal(matchesStatusFilter("cancelled", "failed"), true);
  assert.equal(matchesStatusFilter("completed", "failed"), false);
  assert.equal(anyAiAnswer([{ aiOff: true } as never]), false);
  assert.equal(anyAiAnswer([{ aiOff: true } as never, { aiOff: false } as never]), true);
});
