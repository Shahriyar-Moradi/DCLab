import assert from "node:assert/strict";
import test from "node:test";
import {
  acceptsViaRefs, branchProblem, buildBranchBody, buildChange, changeSetDiff, compareHref, compareRefusal, deltaWording, describeChanges,
  durationText, emptyDraft, mapActionError, metricRows, parseCompareIds, proposedMoves,
} from "./studio-compare.ts";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const err = (status: number, code: string, message = "", details: Record<string, unknown> = {}) => ({ status, body: { error: { code, message, details } } });

test("compare ids: 2..10 distinct UUIDs only", () => {
  assert.deepEqual(parseCompareIds(`${A},${B}`), [A, B]);
  assert.equal(parseCompareIds(A), null);
  assert.equal(parseCompareIds(`${A},${A}`), null);
  assert.equal(parseCompareIds(`${A},nope`), null);
  assert.equal(parseCompareIds(null), null);
  assert.equal(compareHref(A, [A, B]), `/projects/${A}/experiments/compare?ids=${A},${B}`);
  assert.equal(compareHref("x", [A, B]), null);
});

test("a refused comparison is explained in plain words", () => {
  assert.match(compareRefusal(err(409, "split_plan_mismatch")) ?? "", /split differently/);
  assert.match(compareRefusal(err(409, "evidence_missing")) ?? "", /no locked winner/);
  assert.equal(compareRefusal(err(500, "boom")), null);
});

test("metric rows use the shared CV metrics; delta is last minus first", () => {
  const rows = metricRows([{ experiment_id: A, cv: { f1: 0.5, log_loss: 0.7 } }, { experiment_id: B, cv: { f1: 0.6, log_loss: 0.6 } }], ["f1", "log_loss"]);
  assert.equal(Math.round((rows[0].delta ?? 0) * 100), 10);
  assert.equal(deltaWording("f1", rows[0].delta), "better");
  assert.equal(deltaWording("log_loss", rows[1].delta), "better");
  assert.equal(deltaWording("rmse", 0.2), "worse");
  assert.equal(deltaWording("f1", null), "not comparable");
});

test("duration and change-set lines", () => {
  assert.equal(durationText("2026-01-01T00:00:00Z", "2026-01-01T00:00:30Z"), "30 s");
  assert.equal(durationText("2026-01-01T00:00:00Z", "2026-01-01T00:10:00Z"), "10 min");
  assert.equal(durationText(null, "2026-01-01T00:00:30Z"), "—");
  const l = describeChanges({ changes: [{ kind: "family_exclude", family: "xgboost" }] });
  assert.deepEqual(l, ["Exclude a model family: family xgboost"]);
  const d = changeSetDiff(["a", "b"], ["b", "c"]);
  assert.deepEqual([d.onlyLeft, d.onlyRight, d.shared], [["a"], ["c"], ["b"]]);
});

test("branch builder shapes the typed change and leaves rules to the API", () => {
  const hp = { ...emptyDraft(0), family: "random_forest", params: "max_depth=4\nbootstrap=true\ncriterion=gini" };
  assert.deepEqual(buildChange(hp), { kind: "hyperparameter_override", family: "random_forest", parameters: { max_depth: 4, bootstrap: true, criterion: "gini" } });
  assert.equal(typeof buildChange({ ...hp, params: "oops" }), "string");
  assert.deepEqual(buildChange({ ...emptyDraft(1, "class_weighting"), mode: "balanced" }), { kind: "class_weighting", mode: "balanced" });
  assert.deepEqual(buildChange({ ...emptyDraft(1, "class_weighting"), mode: "custom", weights: "yes=3\nno=1" }), { kind: "class_weighting", mode: "custom", weights: { yes: 3, no: 1 } });
  assert.deepEqual(buildChange({ ...emptyDraft(2, "feature_transform_add"), column: "tenure", transform: "impute_median" }), { kind: "feature_transform_add", column: "tenure", transform: "impute_median" });
  const thr = { ...emptyDraft(3, "threshold_objective"), constraint: { metric: "recall", op: ">=" as const, value: "0.8" }, costFn: "5" };
  assert.deepEqual(buildChange(thr), { kind: "threshold_objective", constraints: [{ metric: "recall", op: ">=", value: 0.8 }], cost_false_negative: 5 });
  assert.equal(typeof buildChange({ ...thr, costFn: "x" }), "string");
  assert.equal(typeof buildBranchBody("  ", [hp]), "string");
  assert.equal(typeof buildBranchBody("why", []), "string");
  assert.deepEqual(buildBranchBody(" why ", [{ ...emptyDraft(4, "family_include"), family: "lightgbm" }]), { intent: "why", changes: [{ kind: "family_include", family: "lightgbm" }] });
});

test("the API's change-set refusal is shown with its reason and path", () => {
  const e = branchProblem(err(422, "invalid_change_set", "family not available", { reason: "unknown_family", path: "changes[0].family" }));
  assert.match(e.detail, /unknown_family/);
  assert.match(e.detail, /changes\[0\]\.family/);
});

test("ref and decision conflicts have explicit wording", () => {
  assert.match(mapActionError(err(412, "precondition_failed"), "champion").title, /Someone else moved the champion/);
  assert.match(mapActionError(err(409, "champion_split_plan_mismatch"), "champion").title, /Not comparable/);
  assert.match(mapActionError(err(409, "invalid_decision_transition", "already accepted"), "decision").title, /already resolved/);
  assert.match(mapActionError(err(403, "forbidden"), "decision").title, /cannot do this/);
});

test("proposed ref moves are accepted through the refs endpoint", () => {
  const proposal = { effective_state: "proposed", decision_type: "champion_promoted", details: { ref_moves: [{ ref_kind: "champion_model", to: { id: A } }, { ref_kind: "feature_recipe", to: { id: B } }, { ref_kind: "bad", to: { id: "x" } }] } };
  assert.equal(acceptsViaRefs(proposal), true);
  assert.deepEqual(proposedMoves(proposal), [{ refKind: "champion_model", targetId: A }, { refKind: "feature_recipe", targetId: B }]);
  assert.equal(acceptsViaRefs({ ...proposal, effective_state: "accepted" }), false);
  assert.equal(acceptsViaRefs({ effective_state: "proposed", decision_type: "experiment_accepted" }), false);
});

test("lower-is-better wording covers brier and calibration gap", () => {
  assert.equal(deltaWording("brier", -0.1), "better");
  assert.equal(deltaWording("calibration_gap", 0.1), "worse");
});
