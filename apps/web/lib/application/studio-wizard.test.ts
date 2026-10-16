import assert from "node:assert/strict";
import test from "node:test";

import {
  ActionKeys, buildSpecBody, dataStepProblem, mapWizardError, objectiveProblem, primaryMetricOptions, singleFlight, constraintMetricOptions,
  DIALOG_STEPS, TASK_OPTIONS, dialogStepState, plainWizardError, testDesignRules,
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
  assert.match(mapWizardError(err(409, "split_confirmation_required")).title, /test design/i);
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

test("new project dialog: three steps in order with done / current / to do", () => {
  assert.deepEqual(DIALOG_STEPS.map((s) => s.id), ["file", "goal", "design"]);
  assert.deepEqual(DIALOG_STEPS.map((s) => dialogStepState(s.id, "goal")), ["done", "current", "todo"]);
  assert.deepEqual(DIALOG_STEPS.map((s) => dialogStepState(s.id, "file")), ["current", "todo", "todo"]);
});

test("test design words follow the engine's rule: 20% set aside, stratified for answers, time and IDs win, both stops", () => {
  assert.match(testDesignRules("binary").suggestion, /random 20% .* keeping the share of each answer/);
  assert.match(testDesignRules("multiclass").suggestion, /fewer than 3 rows/);
  assert.doesNotMatch(testDesignRules("regression").suggestion, /share of each answer/);
  const rules = testDesignRules("classification");
  assert.equal(rules.exceptions.length, 3);
  assert.match(rules.exceptions[0], /latest 20%/);
  assert.match(rules.exceptions[1], /whole IDs go to one side/);
  assert.match(rules.exceptions[2], /run stops/);
  assert.match(rules.after, /used once/);
  const banned = /\b(holdout|hold-out|champion|digest|refs?|stratif\w*|L[0-3]|jev|nooa|governance)\b/i;
  for (const task of TASK_OPTIONS.map((o) => o.value)) {
    const r = testDesignRules(task);
    for (const text of [r.suggestion, ...r.exceptions, r.after]) assert.doesNotMatch(text, banned, text);
  }
});

test("New project errors: fixed sentences by status and code only; the server's words never reach the screen", () => {
  const SECRET = "SERVER SAYS internal table ml_jobs failed at line 42";
  const quiet = () => undefined;
  const cases: unknown[] = [
    err(409, "something_new", SECRET), err(422, "validation_failed", SECRET, { errors: [{ loc: ["body", "x"], msg: SECRET }] }), err(429, "quota", SECRET),
    err(403, "forbidden", SECRET), err(404, "not_found", SECRET), err(500, "boom", SECRET), err(400, "__proto__", SECRET), err(413, "", SECRET),
    new Error(SECRET), null, { status: 409, body: { error: { code: "constructor", message: SECRET } } },
  ];
  for (const context of ["project", "upload", "goal", "run", "answer"] as const) {
    for (const c of cases) {
      const out = plainWizardError(c, context, quiet);
      assert.ok(!JSON.stringify(out).includes("SERVER SAYS"), JSON.stringify(out));
      assert.ok(out.title.length > 0 && out.detail.length > 0);
    }
  }
  // The 403 sentence names the refused call.
  assert.match(plainWizardError(err(403, "forbidden"), "project", quiet).detail, /^Creating a project/);
  assert.match(plainWizardError(err(403, "forbidden"), "upload", quiet).detail, /^Uploading a file/);
  assert.match(plainWizardError(err(403, "forbidden"), "run", quiet).detail, /^Starting a run/);
  // Own keys only: __proto__ / constructor are not codes; the status decides.
  assert.equal(plainWizardError(err(409, "__proto__", SECRET), "run", quiet).title, "The request was refused");
  assert.equal(plainWizardError(err(500, "constructor", SECRET), "run", quiet).title, "Something went wrong");
  assert.match(plainWizardError(err(422, "upload_rejected"), "upload", quiet).title, /could not be read/);
  // Developers still get the details in the console.
  const logged: unknown[][] = [];
  plainWizardError(err(500, "boom", SECRET), "run", (...parts) => logged.push(parts));
  assert.ok(JSON.stringify(logged).includes("SERVER SAYS"));
});
