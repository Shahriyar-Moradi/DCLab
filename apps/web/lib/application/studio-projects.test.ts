import assert from "node:assert/strict";
import test from "node:test";
import { NEW_PROJECT_STEPS, STARTING_A_PROJECT, goalWords, lastRunText, modelInUseText, stageOrder, whereItIs } from "./studio-projects.ts";
import { DIALOG_STEPS } from "./studio-wizard.ts";

const BANNED = /\b(release|champion|ref|digest|holdout|manifest|package|slot|tick|revision|window)s?\b/i;

test("where it is: waiting decisions first, then the stage in words", () => {
  assert.deepEqual(whereItIs({ stage: "model_in_use", waiting_decisions: 1 }), [{ text: "1 decision waiting", tone: "warn" }, { text: "Model in use", tone: "ok" }]);
  assert.deepEqual(whereItIs({ stage: "data_uploaded", waiting_decisions: 0 }), [{ text: "File uploaded", tone: "gray" }]);
  assert.deepEqual(whereItIs({ stage: "no_data", waiting_decisions: 3 }).map((p) => p.text), ["3 decisions waiting", "No file yet"]);
  assert.deepEqual(whereItIs({ stage: "running" }), [{ text: "Run in progress", tone: "ai" }]);
  // "Runs finished" is green only when the latest run completed; a failed or stopped one says so.
  assert.deepEqual(whereItIs({ stage: "runs_finished", latest_run: { status: "completed" } }), [{ text: "Runs finished", tone: "ok" }]);
  assert.deepEqual(whereItIs({ stage: "runs_finished", latest_run: { status: "failed" } }), [{ text: "Last run failed", tone: "crit" }]);
  assert.deepEqual(whereItIs({ stage: "runs_finished", latest_run: { status: "cancelled" } }), [{ text: "Runs ended", tone: "gray" }]);
  // A draft goal is "drafted", a locked one "set".
  assert.deepEqual(whereItIs({ stage: "goal_set", goal: { status: "draft" } }), [{ text: "Goal drafted", tone: "gray" }]);
  assert.deepEqual(whereItIs({ stage: "goal_set", goal: { status: "locked" } }), [{ text: "Goal set", tone: "det" }]);
  assert.deepEqual(whereItIs(undefined), []);
  // Unknown or inherited-member stages are shown cleaned, never looked up through the prototype.
  assert.deepEqual(whereItIs({ stage: "__proto__" }), [{ text: "proto", tone: "gray" }]);
  assert.deepEqual(whereItIs({ stage: "new_stage" }), [{ text: "new stage", tone: "gray" }]);
  assert.ok(stageOrder("model_in_use") > stageOrder("running") && stageOrder("running") > stageOrder("no_data"));
  assert.equal(stageOrder("constructor"), -1);
  assert.equal(stageOrder(undefined), -1);
});

test("goal, last run and model in use from the summary; untrusted text is capped plain text", () => {
  assert.deepEqual(goalWords({ objective: "Catch churners", target_column: "churned" }), { objective: "Catch churners", target: "churned" });
  assert.deepEqual(goalWords({ objective: "", target_column: "churned" }), { objective: null, target: "churned" });
  assert.equal(goalWords(null), null);
  const long = goalWords({ objective: `a\u0000b‮${"x".repeat(200)}`, target_column: null })!;
  assert.ok(long.objective!.length <= 120 && !/[\u0000‮]/.test(long.objective!));
  assert.deepEqual(lastRunText({ status: "running", run_number: 6 }), { text: "Run 6 · Running", status: "running" });
  assert.deepEqual(lastRunText({ status: "completed", run_number: null }), { text: "Latest run · Finished", status: "completed" });
  assert.equal(lastRunText(null), null);
  assert.equal(modelInUseText({ champion: { version: "v2" } }), "Model v2");
  assert.equal(modelInUseText({ champion: null }), null);
  assert.equal(modelInUseText(undefined), null);
});

test("the New project dialog has three steps in order, and training starts only after the confirm", () => {
  assert.equal(NEW_PROJECT_STEPS.length, 3);
  assert.deepEqual(NEW_PROJECT_STEPS.map((s) => s.id), DIALOG_STEPS.map((s) => s.id));
  assert.deepEqual(NEW_PROJECT_STEPS.map((s) => s.id), ["file", "goal", "design"]);
  assert.ok(NEW_PROJECT_STEPS.every((s) => s.detail.length > 20));
  assert.match(STARTING_A_PROJECT, /three steps/);
  assert.match(STARTING_A_PROJECT, /only then does training start/);
  assert.match(STARTING_A_PROJECT, /AI switched off/);
});

test("banned words: no Projects sentence or pill uses internal words", () => {
  const texts = [
    STARTING_A_PROJECT, ...NEW_PROJECT_STEPS.map((s) => s.detail),
    ...["no_data", "data_uploaded", "goal_set", "running", "runs_finished", "model_in_use"].flatMap((stage) => ["completed", "failed", "cancelled"].flatMap((status) => whereItIs({ stage, waiting_decisions: 2, latest_run: { status } }).map((p) => p.text))),
  ];
  for (const t of texts) assert.doesNotMatch(t, BANNED, t);
});
