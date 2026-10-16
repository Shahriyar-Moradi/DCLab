import assert from "node:assert/strict";
import test from "node:test";
import { FLOW_TILE_ORDER, flowTargets, flowTiles, type ProjectFlowLike } from "./studio-flow-tiles.ts";
import { stepScreenHref } from "./studio-flow.ts";

const P = "11111111-1111-4111-8111-111111111111";
const SPLIT = "22222222-2222-4222-8222-222222222222";
const MODEL = "33333333-3333-4333-8333-333333333333";
const ALL = new Set(FLOW_TILE_ORDER.map((s) => s.id));
const BANNED = /\b(release|champion|ref|digest|holdout|manifest|package|slot|tick|revision|window|psi)s?\b|final test/i;

const full = (): ProjectFlowLike => ({
  data: { state: "done", file_name: "churn_2026.csv", row_count: 48210, newer_file: false },
  goal: { state: "done", goal: { target_column: "churned", objective: "Catch churners" }, split_plan_id: SPLIT, validation_folds: 5 },
  experiments: { state: "done", run_count: 6, running_count: 0, best_run_number: 3, best_cv_score: 0.7049, selection_metric: "pr_auc" },
  improve: { state: "done", branch_run_count: 2 },
  model: { state: "done", model_version_id: MODEL, version: "v2", model_family: "lightgbm", algorithm: "lightgbm", decision_threshold: 0.31, run_number: 3 },
  predictions: { state: "done", completed_scorings: 4, last_rows_scored: 6412, last_scored_at: "2026-10-05T02:00:00Z", schedule_enabled: true },
  monitoring: { state: "alert", status: "alert", window_start: "2026-10-12T00:00:00Z" },
  history: { state: "done", decision_count: 12, last_decision_at: "2026-10-05T09:00:00Z" },
});
const empty = (): ProjectFlowLike => ({
  data: { state: "todo" }, goal: { state: "todo", goal: null }, experiments: { state: "todo", run_count: 0 }, improve: { state: "todo", branch_run_count: 0 },
  model: { state: "todo" }, predictions: { state: "todo", completed_scorings: 0 }, monitoring: { state: "todo" }, history: { state: "todo", decision_count: 0 },
});
const tiles = (flow: ProjectFlowLike, pathname = "/home", available: ReadonlySet<string> = ALL) =>
  flowTiles(flow, { projectId: P, pathname, available, targets: flowTargets(flow) });
const byId = (list: ReturnType<typeof tiles>) => Object.fromEntries(list.map((t) => [t.id, t]));

test("eight tiles in flow order, numbered 1-8, each with a value and a note from the flow read", () => {
  const t = byId(tiles(full()));
  assert.deepEqual(Object.keys(t), ["data", "goal", "experiments", "improve", "models", "predictions", "monitoring", "history"]);
  assert.deepEqual(Object.values(t).map((x) => x.number), [1, 2, 3, 4, 5, 6, 7, 8]);
  assert.deepEqual([t.data.value, t.data.note], ["churn_2026.csv", "48,210 rows"]);
  assert.deepEqual([t.goal.value, t.goal.note], ["Predict churned", "5-fold cross-validation"]);
  assert.deepEqual([t.experiments.value, t.experiments.note], ["6 runs", "best of recent runs: Run 3, CV PR-AUC 0.70"]);
  assert.deepEqual([t.improve.value, t.improve.note], ["2 tries", "runs branched from another run"]);
  assert.deepEqual([t.models.value, t.models.note], ["LightGBM", "Model v2 · threshold 0.31"]);
  assert.deepEqual([t.predictions.value, t.predictions.note], ["6,412 rows scored", "weekly scoring on"]);
  assert.deepEqual([t.monitoring.value, t.monitoring.note, t.monitoring.state], ["Inputs look different", "week of 12 Oct 2026 (UTC)", "alert"]);
  assert.deepEqual([t.history.value, t.history.note], ["12 decisions", "last on 5 Oct 2026 (UTC)"]);
  assert.equal(t.monitoring.stateWords, "needs a look");
});

test("links come from UUID ids only; Goal needs the test design, Predictions the model in use; History opens the decision log", () => {
  const t = byId(tiles(full()));
  assert.equal(t.goal.href, `/projects/${P}/splits/${SPLIT}`);
  assert.equal(t.predictions.href, `/projects/${P}/predictions`);
  assert.equal(t.history.href, `/projects/${P}/decisions`);
  assert.equal(t.models.href, `/projects/${P}/models`);
  const none = byId(tiles(empty()));
  // Without a test design the Goal tile opens whatever the shared step link says (the flow bar and sidebar agree).
  assert.equal(none.goal.href, stepScreenHref("goal", P, { split_plan: null }));
  assert.equal(none.predictions.href, null);
  const bad = flowTiles(full(), { projectId: "../x", pathname: "/home", available: ALL, targets: { split_plan: "javascript:1", project_model: MODEL } });
  assert.ok(bad.every((x) => x.href === null));
  assert.equal(stepScreenHref("history", "not-a-uuid", {}), null);
});

test("an empty project says what to do on each tile, and nothing is claimed done", () => {
  const t = byId(tiles(empty()));
  assert.ok(Object.values(t).every((x) => x.state === "todo" && x.stateWords === "not started"));
  assert.deepEqual([t.data.value, t.goal.value, t.experiments.value, t.models.value, t.predictions.value, t.monitoring.value, t.history.value],
    ["No file yet", "Not set yet", "No runs yet", "None in use", "Not scored yet", "Nothing yet", "Nothing yet"]);
});

test("in-progress, partial and edge facts", () => {
  const f = full();
  f.data = { state: "done", file_name: "a\u0000b‮.csv " + "x".repeat(80), row_count: null, newer_file: true };
  f.goal = { state: "in_progress", goal: { target_column: null }, split_plan_id: null };
  f.experiments = { state: "in_progress", run_count: 1, running_count: 1, best_cv_score: null, selection_metric: null };
  f.model = { state: "done", model_version_id: MODEL, version: "v4", model_family: null, algorithm: null, decision_threshold: null };
  f.predictions = { state: "in_progress", completed_scorings: 0, schedule_enabled: null };
  f.monitoring = { state: "done", status: "not_enough_data", window_start: null };
  f.experiments.selection_metric = "rmse";
  f.experiments.best_cv_score = -2.5; // a stored negated error is shown as a positive error
  const t = byId(tiles(f));
  assert.ok(t.data.value.length <= 40 && !/[\u0000‮]/.test(t.data.value), t.data.value);
  assert.equal(t.data.note, "rows not counted · a newer file is uploaded");
  assert.deepEqual([t.goal.value, t.goal.note], ["Set, no column yet", "no test design in use yet"]);
  assert.deepEqual([t.experiments.note, t.experiments.state], ["1 running · best of recent runs: CV RMSE 2.50", "in_progress"]);
  assert.deepEqual([t.models.value, t.models.note], ["Model v4", "in use"]);
  assert.deepEqual([t.predictions.value, t.predictions.note], ["Scoring now", "the first file is being scored"]);
  assert.deepEqual([t.monitoring.value, t.monitoring.note], ["Too few rows yet", "newest week"]);
});

test("server values are looked up by own keys only: __proto__ states and statuses fall back", () => {
  const f = full();
  f.data.state = "__proto__";
  f.monitoring = { state: "constructor", status: "__proto__", window_start: null };
  const t = byId(tiles(f));
  assert.deepEqual([t.data.state, t.data.stateWords], ["unknown", ""]);
  assert.deepEqual([t.monitoring.state, t.monitoring.value], ["unknown", "Checked"]);
});

test("the current step follows the route (the score tab is Predictions; the decision log is History)", () => {
  const current = (path: string, tab?: string) => flowTiles(full(), { projectId: P, pathname: path, tab, available: ALL, targets: flowTargets(full()) }).filter((x) => x.current).map((x) => x.id);
  assert.deepEqual(current(`/projects/${P}/data`), ["data"]);
  assert.deepEqual(current(`/projects/${P}/splits/${SPLIT}`), ["goal"]);
  assert.deepEqual(current(`/projects/${P}/models/${MODEL}`, "score"), ["predictions"]);
  assert.deepEqual(current(`/projects/${P}/decisions`), ["history"]);
  assert.deepEqual(current("/home"), []);
});

test("a step whose screen is not built is not listed; the numbers stay 1-8", () => {
  const list = tiles(full(), "/home", new Set(["data", "experiments", "history"]));
  assert.deepEqual(list.map((x) => [x.id, x.number]), [["data", 1], ["experiments", 3], ["history", 8]]);
});

test("no tile's words name another step (links are named by their step) and no internal or final-test words", () => {
  const labels = FLOW_TILE_ORDER.map((s) => s.label.toLowerCase());
  for (const flow of [full(), empty()]) {
    for (const tile of tiles(flow)) {
      const words = `${tile.value} ${tile.note} ${tile.stateWords}`;
      for (const label of labels) if (label !== tile.label.toLowerCase() && !(tile.id === "goal" && label === "goal & test design")) assert.ok(!words.toLowerCase().includes(label), `${tile.id}: ${words} names ${label}`);
      assert.doesNotMatch(words, BANNED, words);
    }
  }
});
