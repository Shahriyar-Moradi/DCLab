import assert from "node:assert/strict";
import test from "node:test";
import {
  buildChoice, chartMode, defaultThreshold, chartRows, chooseGate, chosenSummary, constraintLines, focusedPoint, foldSpreadText, mapChooseError, operatingView, pointRows, pointSentence, reasonProblem,
} from "./studio-operating.ts";

const pt = (threshold: number, extra: Record<string, unknown> = {}) => ({
  threshold, tp: 10, fp: 5, fn: 3, tn: 80, precision: 0.6, recall: 0.7, specificity: 0.9, f1: 0.65, accuracy: 0.9, balanced_accuracy: 0.8, flagged_share: 0.12, ...extra,
});
const detail = (threshold: number, extra: Record<string, unknown> = {}) => ({ ...pt(threshold), what_this_means: `At threshold ${threshold} the model flags 12% of rows`, ...extra });
const base = (extra: Record<string, unknown> = {}) => ({
  experiment_id: "e", status: "available", min_class_rows: 20, tie_break: "tb", points: [pt(0.9), pt(0.3), pt(0.5)], pareto: [detail(0.3)],
  locked: { threshold: 0.5, point: detail(0.5) }, chosen: null, ...extra,
}) as never;

test("states: available, not applicable, no curve, too few rows (names the minimum)", () => {
  assert.equal(operatingView(base()).state, "available");
  const na = operatingView(base({ status: "not_applicable", points: [] }));
  assert.ok(na.state === "empty" && /yes\/no/.test(na.text));
  const old = operatingView(base({ status: "not_available", points: [] }));
  assert.ok(old.state === "empty" && /predates/.test(old.text));
  const few = operatingView(base({ status: "not_evaluated", points: [] }));
  assert.ok(few.state === "empty" && /At least 20 rows/.test(few.text));
  assert.equal(operatingView(base({ points: [] })).state, "empty");
});

test("curve to series: ascending thresholds, thinned but marked points kept, cost mode only with a full cost column", () => {
  const rows = chartRows(base());
  assert.deepEqual(rows.map((r) => r.threshold), [0.3, 0.5, 0.9]);
  const many = Array.from({ length: 1000 }, (_, i) => pt(Number((i / 1000).toFixed(4))));
  const thin = chartRows(base({ points: many, pareto: [detail(0.457)], chosen: { threshold: 0.333, decision_id: "d", method: "threshold", rationale: "r", recorded_at: "t" }, locked: { threshold: 0.7 } }), 100);
  assert.ok(thin.length < 250);
  for (const t of [0.457, 0.333, 0.7, 0.999]) assert.ok(thin.some((r) => r.threshold === t), `kept ${t}`);
  assert.equal(chartMode(base()), "precision_recall");
  assert.equal(chartMode(base({ cost_matrix: { false_positive: 1, false_negative: 5 } })), "precision_recall");
  assert.equal(chartMode(base({ cost_matrix: { false_positive: 1, false_negative: 5 }, points: [pt(0.1, { expected_cost: 0.2 })] })), "expected_cost");
});

test("constraint lines only from the chosen objective's precision/recall constraints", () => {
  assert.deepEqual(constraintLines(base()), []);
  const lines = constraintLines(base({ chosen: { objective: { goal: "f1", constraints: [{ metric: "recall", op: ">=", value: 0.7 }, { metric: "flagged_share", op: "<=", value: 0.2 }] } } }));
  assert.equal(lines.length, 1);
  assert.equal(lines[0].metric, "recall");
});

test("point rows tag best trade-off, locked and chosen; one row per threshold", () => {
  const rows = pointRows(base({ chosen: { threshold: 0.3, decision_id: "d", method: "threshold", rationale: "r", recorded_at: "t", point: detail(0.3) } }));
  assert.deepEqual(rows.map((r) => [r.threshold, r.tags]), [[0.3, ["Best trade-off", "Chosen"]], [0.5, ["Locked"]]]);
});

test("sentence is the server's text; fallback restates fields without computing", () => {
  const data = base();
  assert.match(pointSentence(focusedPoint(data, 0.3)), /flags 12% of rows/);
  assert.match(pointSentence(focusedPoint(data, 0.9)), /^At threshold 0\.9: flagged share 0\.120, recall 0\.700/);
  assert.equal(focusedPoint(data, null).label, "Locked point");
  assert.equal(focusedPoint(base({ chosen: { threshold: 0.3, point: detail(0.3) } }), null).label, "Chosen point");
});

test("fold spread lines come from the API's ranges", () => {
  assert.deepEqual(foldSpreadText(null), []);
  const lines = foldSpreadText(detail(0.5, { fold_spread: { folds: 5, recall_folds: 5, precision_folds: 4, recall_min: 0.6, recall_max: 0.8, precision_min: 0.5, precision_max: 0.7, includes_folds_outside_curve: true } }) as never);
  assert.equal(lines.length, 3);
  assert.match(lines[0], /Recall across 5 of 5 folds: 0\.600 to 0\.800/);
});

test("action gate: viewers and unavailable curves are blocked with a reason", () => {
  assert.equal(chooseGate(base(), true).allowed, true);
  assert.match(chooseGate(base(), false).reason ?? "", /Ask a workspace member/);
  assert.equal(chooseGate(base({ status: "not_applicable" }), true).allowed, false);
  assert.equal(chooseGate(undefined, true).allowed, false);
});

test("choice body: reason required and limited, threshold exact, degenerate objective refused locally", () => {
  assert.ok(reasonProblem("  ") !== null);
  assert.ok(reasonProblem("x".repeat(4001)) !== null);
  assert.equal(reasonProblem("because"), null);
  assert.deepEqual(buildChoice({ kind: "threshold", threshold: 0.3 }, " why "), { body: { threshold: 0.3, reason: "why" } });
  assert.ok("problem" in buildChoice({ kind: "threshold", threshold: 0.3 }, ""));
  assert.ok("problem" in buildChoice({ kind: "objective", goal: "recall", metric: "recall", op: ">=", value: "0.5" }, "why"));
  assert.ok("problem" in buildChoice({ kind: "objective", goal: "f1", metric: "recall", op: ">=", value: "2" }, "why"));
  assert.deepEqual(buildChoice({ kind: "objective", goal: "recall", metric: "precision", op: ">=", value: "0.5" }, "why"), { body: { objective: { goal: "recall", constraints: [{ metric: "precision", op: ">=", value: 0.5 }] }, reason: "why" } });
  assert.deepEqual(buildChoice({ kind: "objective", goal: "f1", metric: "recall", op: ">=", value: "" }, "why"), { body: { objective: { goal: "f1", constraints: [] }, reason: "why" } });
});

test("error mapping: 422 codes, infeasible shows the closest candidate, 403, 409", () => {
  const err = (status: number, code: string, details: unknown = {}, message = "") => ({ status, body: { error: { code, message, details } } });
  assert.match(mapChooseError(err(422, "threshold_not_on_curve")).title, /not on the stored curve/);
  const inf = mapChooseError(err(422, "objective_infeasible", { closest: { threshold: 0.4, recall: 0.6, precision: 0.5 } }));
  assert.match(inf.title, /No threshold meets every constraint/);
  assert.equal(inf.closest?.threshold, 0.4);
  assert.match(inf.detail, /Closest candidate: threshold 0\.4/);
  assert.match(mapChooseError(err(409, "operating_points_not_available")).title, /not available/);
  assert.match(mapChooseError(err(403, "forbidden")).title, /cannot choose/);
  assert.match(mapChooseError(err(403, "human_session_required")).title, /Only people/);
  assert.match(mapChooseError(err(409, "idempotency_key_conflict")).title, /already used/);
});

test("model card summary: only with a chosen point; untrusted reason passed through unchanged", () => {
  assert.equal(chosenSummary(base()), null);
  const s = chosenSummary(base({ chosen: { decision_id: "d1", threshold: 0.3, method: "objective", rationale: "<b>x</b>", recorded_at: "2026-01-01T00:00:00Z", chosen_by_user_id: "u1", point: detail(0.3), curve_changed: true } }));
  assert.equal(s?.reason, "<b>x</b>");
  assert.equal(s?.threshold, "0.3");
  assert.match(s?.method ?? "", /goal/);
  assert.match(s?.note ?? "", /curve changed/);
  assert.equal(chosenSummary(base({ status: "not_available", chosen: { threshold: 1 } })), null);
});

test("default picker value is always a listed threshold", () => {
  assert.equal(defaultThreshold(base()), 0.5); // locked and listed
  assert.equal(defaultThreshold(base({ chosen: { threshold: 0.3 } })), 0.3);
  assert.equal(defaultThreshold(base({ chosen: { threshold: 0.31 }, locked: { threshold: 0.5 } })), 0.5); // chosen not listed
  assert.equal(defaultThreshold(base({ chosen: { threshold: 0.31 }, locked: { threshold: 0.51 } })), 0.9); // neither listed: first point
  assert.ok(Number.isNaN(defaultThreshold(base({ points: [] }))));
});

test("server error text is flattened to plain text and capped", () => {
  const out = mapChooseError({ status: 422, body: { error: { code: "validation_failed", message: `x\n${"y".repeat(500)}` } } });
  assert.ok(out.detail.length <= 300 && !out.detail.includes("\n"));
});
