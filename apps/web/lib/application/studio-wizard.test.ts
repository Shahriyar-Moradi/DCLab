import assert from "node:assert/strict";
import test from "node:test";

import {
  ActionKeys, buildSpecBody, dataStepProblem, mapWizardError, objectiveProblem, primaryMetricOptions, singleFlight, constraintMetricOptions,
} from "./studio-wizard.ts";

const err = (status: number, code: string, message = "", details: unknown = {}) => ({ status, body: { error: { code, message, details } } });
const draft = { primaryMetric: "", constraint: null, businessObjective: "" };

test("spec body: only contract fields, locked, empty target means the rule decides", () => {
  const body = buildSpecBody("binary", " churn ", { primaryMetric: "pr_auc", constraint: { metric: "recall", op: ">=", value: "0.8" }, businessObjective: " Cut churn " });
  assert.deepEqual(body, {
    task_type: "binary", business_objective: "Cut churn", target_column: "churn", primary_metric: "pr_auc",
    constraints: { metric_constraints: [{ metric: "recall", op: ">=", value: 0.8 }] }, status: "locked",
  });
  const open = buildSpecBody("classification", "  ", draft);
  assert.equal(open.target_column, null);
  assert.equal(open.primary_metric, null);
  assert.deepEqual(open.constraints, {});
  assert.ok(open.business_objective.length > 0);
});

test("objective and data step validation", () => {
  assert.equal(objectiveProblem(draft), null);
  assert.match(objectiveProblem({ ...draft, constraint: { metric: "recall", op: ">=", value: "" } })!, /number/);
  assert.match(objectiveProblem({ ...draft, constraint: { metric: "recall", op: ">=", value: "abc" } })!, /number/);
  assert.equal(objectiveProblem({ ...draft, constraint: { metric: "recall", op: "<=", value: "0.2" } }), null);
  assert.match(dataStepProblem({ projectId: null, projectName: "", file: null, datasetId: null })!, /file/);
  assert.match(dataStepProblem({ projectId: null, projectName: " ", file: {} as File, datasetId: null })!, /Name/);
  assert.equal(dataStepProblem({ projectId: "p", projectName: "", file: {} as File, datasetId: null }), null);
  assert.equal(dataStepProblem({ projectId: "p", projectName: "", file: null, datasetId: "d" }), null);
});

test("metric options follow the task; precision/recall are constraints only", () => {
  assert.ok(!primaryMetricOptions("binary").includes("recall"));
  assert.ok(constraintMetricOptions("binary").includes("recall"));
  assert.ok(primaryMetricOptions("regression").includes("rmse"));
  assert.ok(!primaryMetricOptions("regression").includes("pr_auc"));
  assert.ok(constraintMetricOptions("regression").includes("mape"));
});

test("idempotency: same action and input keeps the key, a changed input or a success makes a new one", () => {
  let n = 0;
  const keys = new ActionKeys(() => `k${++n}`);
  const first = keys.keyFor("upload", "a.csv:10");
  assert.equal(keys.keyFor("upload", "a.csv:10"), first);
  assert.notEqual(keys.keyFor("upload", "b.csv:10"), first);
  const spec = keys.keyFor("spec", "x");
  keys.done("spec");
  assert.notEqual(keys.keyFor("spec", "x"), spec);
});

test("single flight: a double submit runs once and a later submit runs again", async () => {
  let calls = 0;
  let release: () => void = () => {};
  const submit = singleFlight(() => new Promise<number>((resolve) => { calls += 1; release = () => resolve(calls); }));
  const a = submit();
  const b = submit();
  assert.equal(a, b);
  release();
  assert.equal(await a, 1);
  const c = submit();
  release();
  assert.equal(await c, 2);
});

test("backend codes map to plain language; unknown codes keep the backend message", () => {
  assert.match(mapWizardError(err(422, "TARGET_NOT_IN_DATASET")).title, /target column/i);
  assert.match(mapWizardError(err(422, "plan_refused")).title, /plan/i);
  assert.match(mapWizardError(err(409, "split_confirmation_required")).title, /split/i);
  assert.match(mapWizardError(err(409, "target_confirmation_required")).title, /target/i);
  assert.match(mapWizardError(err(422, "upload_rejected")).title, /could not be read/);
  assert.match(mapWizardError(err(409, "idempotency_key_conflict")).title, /already used/);
  const v = mapWizardError(err(422, "validation_failed", "x", { errors: [{ loc: ["body", "target_column"], msg: "bad" }] }));
  assert.equal(v.detail, "target_column: bad");
  assert.equal(mapWizardError(err(409, "something_new", "Backend says no")).detail, "Backend says no");
  assert.match(mapWizardError(err(429, "run_quota_exceeded", "Quota")).title, /Too many/);
  assert.match(mapWizardError(err(403, "forbidden")).title, /cannot/);
  assert.equal(mapWizardError(new Error("boom")).detail, "boom");
  assert.equal(mapWizardError(null).title, "Something went wrong");
});

test("input problems found before sending are plain, fixable answers", async () => {
  const { WizardInputError } = await import("./studio-wizard.ts");
  assert.deepEqual(mapWizardError(new WizardInputError("Choose a data file to upload.")), { title: "Check your answers", detail: "Choose a data file to upload.", fixable: true });
});
