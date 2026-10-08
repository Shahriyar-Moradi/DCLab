import assert from "node:assert/strict";
import test from "node:test";

import { buildFlow, flowRunStatuses, stepForPath, type FlowInput } from "./studio-flow.ts";

const P = "11111111-1111-4111-8111-111111111111";
const SPLIT = "22222222-2222-4222-8222-222222222222";
const MODEL = "33333333-3333-4333-8333-333333333333";
const base: FlowInput = {
  projectId: P, pathname: `/projects/${P}/experiments`,
  available: new Set(["data", "goal", "experiments", "models", "predictions"]),
  refKinds: ["dataset", "problem_spec", "split_plan"], runStatuses: ["completed"], targets: { split_plan: SPLIT },
};
const states = (steps: ReturnType<typeof buildFlow>) => Object.fromEntries(steps.map((s) => [s.id, s.state]));

test("only built steps are listed, in project order", () => {
  assert.deepEqual(buildFlow(base).map((s) => s.id), ["data", "goal", "experiments", "models", "predictions"]);
});

test("states come from refs, runs and the route; the first open step is next", () => {
  assert.deepEqual(states(buildFlow(base)), { data: "done", goal: "done", experiments: "current", models: "next", predictions: null });
});

test("unknown reads give no state instead of a guess", () => {
  const steps = buildFlow({ ...base, pathname: `/projects/${P}/data`, refKinds: null, runStatuses: null });
  assert.deepEqual(states(steps), { data: "current", goal: null, experiments: null, models: null, predictions: null });
});

test("steps whose page needs an id link only when the project has one", () => {
  const steps = buildFlow({ ...base, targets: { split_plan: SPLIT, champion_model: MODEL } });
  const href = Object.fromEntries(steps.map((s) => [s.id, s.href]));
  assert.equal(href.goal, `/projects/${P}/splits/${SPLIT}`);
  assert.equal(href.predictions, `/projects/${P}/models/${MODEL}?tab=score`);
  assert.equal(href.data, `/projects/${P}/data`);
  const none = buildFlow({ ...base, targets: { split_plan: "not-a-uuid" } });
  assert.equal(none.find((s) => s.id === "goal")?.href, null);
  assert.equal(none.find((s) => s.id === "predictions")?.href, null);
});

test("routes map to steps; History and unknown paths belong to none", () => {
  assert.equal(stepForPath(`/projects/${P}/pipeline/abc`, P), "experiments");
  assert.equal(stepForPath(`/projects/${P}/splits/${SPLIT}`, P), "goal");
  assert.equal(stepForPath(`/projects/${P}/models/${MODEL}`, P), "models");
  assert.equal(stepForPath(`/projects/${P}/decisions`, P), null);
  assert.equal(stepForPath("/projects/other/data", P), null);
});

test("a partial run list never claims there is no completed run", () => {
  assert.equal(flowRunStatuses(undefined, false), null);
  assert.deepEqual(flowRunStatuses([{ status: "failed" }], false), ["failed"]);
  assert.equal(flowRunStatuses([{ status: "failed" }], true), null);
  assert.deepEqual(flowRunStatuses([{ status: "failed" }, { status: "completed" }], true), ["failed", "completed"]);
  const unknown = buildFlow({ ...base, pathname: `/projects/${P}/data`, runStatuses: flowRunStatuses([{ status: "failed" }], true) });
  assert.equal(unknown.find((s) => s.id === "experiments")?.state, null);
});

test("the score tab of a model is the Predictions step", () => {
  assert.equal(stepForPath(`/projects/${P}/models/${MODEL}`, P, "score"), "predictions");
  assert.equal(stepForPath(`/projects/${P}/models/${MODEL}`, P, "card"), "models");
  const steps = buildFlow({ ...base, pathname: `/projects/${P}/models/${MODEL}`, tab: "score", targets: { split_plan: SPLIT, champion_model: MODEL } });
  assert.equal(steps.find((s) => s.id === "predictions")?.state, "current");
});
