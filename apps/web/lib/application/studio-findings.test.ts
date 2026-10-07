import assert from "node:assert/strict";
import test from "node:test";
import {
  attentionCount, branchPrefillHref, findingStatusLabel, findingTone, findingsState, formatEvidenceValue, evidenceRows, parseBranchPrefill, prefillFor, sortFindings,
} from "./studio-findings.ts";

const P = "11111111-1111-4111-8111-111111111111";
const E = "22222222-2222-4222-8222-222222222222";
const f = (check: string, status: string, severity = "warning", extra: Record<string, unknown> = {}) => ({ check, status, severity, message: `${check} message`, ...extra });

test("findings sort worst first: fail (critical before error), warning, not evaluated, pass", () => {
  const sorted = sortFindings([f("a", "pass", "info"), f("b", "not_evaluated", "info"), f("c", "warning"), f("d", "fail", "error"), f("e", "fail", "critical")]);
  assert.deepEqual(sorted.map((x) => x.check), ["e", "d", "c", "b", "a"]);
});

test("status is text as well as colour", () => {
  assert.equal(findingTone("fail"), "crit");
  assert.equal(findingTone("warning"), "warn");
  assert.equal(findingTone("pass"), "ok");
  assert.equal(findingTone("not_evaluated"), "gray");
  assert.equal(findingStatusLabel("fail", "critical"), "Failed (critical)");
  assert.equal(findingStatusLabel("not_evaluated", "info"), "Not evaluated");
  assert.equal(findingStatusLabel("weird_new", "info"), "weird new");
});

test("empty states: not computed is not the same as all passed", () => {
  assert.equal(findingsState(undefined), "pending");
  assert.equal(findingsState({ investigated: false, checks: [] }), "not_computed");
  assert.equal(findingsState({ investigated: true, checks: [f("a", "pass", "info"), f("b", "pass", "info")] }), "all_passed");
  assert.equal(findingsState({ investigated: true, checks: [f("a", "pass", "info"), f("b", "not_evaluated", "info")] }), "attention");
  assert.equal(attentionCount({ investigated: true, checks: [f("a", "warning"), f("b", "fail"), f("c", "pass", "info"), f("d", "not_evaluated", "info")] }), 2);
});

test("evidence formatting is unit aware and keeps unknown keys as plain text", () => {
  assert.equal(formatEvidenceValue("minority_class_fraction", 0.0625), "6.3%");
  assert.equal(formatEvidenceValue("train_duplicate_rows", 12345), "12,345");
  assert.equal(formatEvidenceValue("cv_score", 0.912345), "0.912");
  assert.equal(formatEvidenceValue("x", 1234.567), "1234.6");
  assert.equal(formatEvidenceValue("fail_eligible", true), "yes");
  assert.equal(formatEvidenceValue("cv_std", null), "n/a");
  assert.equal(formatEvidenceValue("risky_columns", ["a", "b"]), "a, b");
  assert.equal(formatEvidenceValue("risky_columns", []), "none");
  assert.equal(formatEvidenceValue("direction", "higher_is_better"), "higher is better");
  assert.equal(formatEvidenceValue("winner_family", "random_forest"), "random_forest");
  assert.match(formatEvidenceValue("columns", [{ column: "x" }]), /^1 item: /);
  assert.equal(formatEvidenceValue("future_key", "<img src=x onerror=alert(1)>"), "<img src=x onerror=alert(1)>");
  assert.equal(formatEvidenceValue("note", "z".repeat(500)).length, 301);
  assert.deepEqual(evidenceRows({ minority_rows: 3 }), [{ key: "minority_rows", label: "Minority rows", value: "3" }]);
  assert.deepEqual(evidenceRows(undefined), []);
});

test("recommendations map to typed branch changes only when expressible", () => {
  assert.equal(prefillFor(f("class_imbalance", "warning", "warning", { recommendation_kind: "class_weights" }))?.prefill, "class_weighting");
  assert.equal(prefillFor(f("overfit_gap", "warning", "warning", { recommendation_kind: "simpler_model", evidence: { winner_family: "random_forest" } }))?.family, "random_forest");
  assert.equal(prefillFor(f("overfit_gap", "warning", "warning", { recommendation_kind: "regularize", evidence: { winner_family: "xgboost" } }))?.prefill, "hyperparameter_override");
  const leak = prefillFor(f("target_leakage", "fail", "error", { recommendation_kind: "review_columns", evidence: { risky_columns: ["customer_code", "b"] } }));
  assert.equal(leak?.prefill, "feature_transform_add");
  assert.equal(leak?.column, "customer_code");
  for (const kind of ["deduplicate", "collect_more_data", "something_new", null]) assert.equal(prefillFor(f("x", "warning", "warning", { recommendation_kind: kind })), null);
});

test("branch href carries a validated pre-fill and round-trips through the parser", () => {
  const finding = f("target_leakage", "fail", "error", { recommendation_kind: "review_columns", evidence: { risky_columns: ["customer code&x=1"] } });
  const href = branchPrefillHref(P, E, finding);
  assert.ok(href?.startsWith(`/projects/${P}/experiments/${E}?prefill=feature_transform_add`));
  assert.ok(href?.endsWith("#branch"));
  const query = new URLSearchParams(href!.split("?")[1].split("#")[0]);
  const parsed = parseBranchPrefill(query);
  assert.equal(parsed?.draft.kind, "feature_transform_add");
  assert.equal(parsed?.draft.column, "customer code&x=1");
  assert.equal(parsed?.draft.transform, "drop_column");
  assert.equal(branchPrefillHref("nope", E, finding), null);
  assert.equal(branchPrefillHref(P, E, f("x", "warning", "warning", { recommendation_kind: "deduplicate" })), null);
});

test("parsing refuses unknown kinds and clamps hostile values", () => {
  assert.equal(parseBranchPrefill(new URLSearchParams("prefill=drop_database")), null);
  assert.equal(parseBranchPrefill(new URLSearchParams("")), null);
  const parsed = parseBranchPrefill(new URLSearchParams(`prefill=class_weighting&mode=evil&transform=rm&family=${"x".repeat(500)}`));
  assert.equal(parsed?.draft.mode, "balanced");
  assert.equal(parsed?.draft.transform, "drop_column");
  assert.equal(parsed?.draft.family.length, 120);
});
