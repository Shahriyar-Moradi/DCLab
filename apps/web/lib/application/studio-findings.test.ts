import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import {
  CHECK_LABEL, CHECK_ORDER, checkList, passSummary, RECOMMENDATION_TEXT, attentionCount, branchPrefillHref, checkLabel, findingStatusLabel, findingTone, findingsState, formatEvidenceValue, evidenceRows,
  parseBranchPrefill, prefillFor, sortFindings,
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
  assert.equal(findingStatusLabel("not_evaluated", "info"), "Not checked");
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
  const nested = evidenceRows({ holdout_rows: 40, holdout_comparison: { drifted_columns: ["amount"], max_psi: 0.31 } });
  assert.deepEqual(nested.map((r) => [r.key, r.label, r.value]), [
    ["holdout_rows", "Final test set rows", "40"],
    ["holdout_comparison.drifted_columns", "Test rows: drifted columns", "amount"],
    ["holdout_comparison.max_psi", "Test rows: max psi", "0.31"],
  ]);
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

test("P5.1-A checks have labels, recommendation text and only expressible pre-fills", () => {
  const added = ["fold_instability", "calibration", "subgroup_gap", "multicollinearity", "feature_drift", "temporal_shift",
    "missingness_shift", "contamination", "time_travel", "new_feature"];
  for (const check of added) assert.ok(CHECK_LABEL[check] && checkLabel(check) !== check.replaceAll("_", " "), check);
  for (const kind of ["calibrate", "review_subgroups", "drop_correlated", "review_split"]) assert.ok(RECOMMENDATION_TEXT[kind], kind);
  const collinear = prefillFor(f("multicollinearity", "warning", "warning", { recommendation_kind: "drop_correlated", evidence: { drop_candidates: ["a_copy"] } }));
  assert.deepEqual([collinear?.prefill, collinear?.transform, collinear?.column], ["feature_transform_add", "drop_column", "a_copy"]);
  const jump = prefillFor(f("new_feature", "fail", "error", { recommendation_kind: "investigate_leakage", evidence: { added_feature: "refund_flag" } }));
  assert.equal(jump?.column, "refund_flag");
  assert.equal(prefillFor(f("time_travel", "fail", "critical", { recommendation_kind: "review_columns", evidence: { moved_columns: ["prior_orders"] } }))?.column, "prior_orders");
  for (const kind of ["calibrate", "review_subgroups", "review_split"]) assert.equal(prefillFor(f("x", "warning", "warning", { recommendation_kind: kind })), null);
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

const openapi = JSON.parse(readFileSync(new URL("../infrastructure/v1/openapi.json", import.meta.url), "utf8")) as {
  components: { schemas: { ExperimentFindingRead: { properties: { check: { enum: string[] } } } } };
};

test("the check registry is the server's: the same 15 checks in the same order as the API's enum", () => {
  assert.deepEqual([...CHECK_ORDER], openapi.components.schemas.ExperimentFindingRead.properties.check.enum);
  assert.equal(CHECK_ORDER.length, 15);
  for (const check of CHECK_ORDER) assert.ok(Object.hasOwn(CHECK_LABEL, check), check);
});

test("check list: every registry check in run order; a check the run did not record is never 'Passed'", () => {
  assert.deepEqual(checkList(null), []);
  assert.deepEqual(checkList({ investigated: false, checks: [] }), []);
  // An older run stored the five core checks only (the read sends them in registry order).
  const old = checkList({ investigated: true, checks: [f("target_leakage", "pass", "info"), f("overfit_gap", "warning"), f("duplicate_rows", "fail", "critical"), f("class_imbalance", "not_evaluated", "info"), f("implausible_score", "pass", "info")] });
  assert.equal(old.length, 15);
  assert.deepEqual(old.map((r) => r.check), [...CHECK_ORDER]);
  assert.deepEqual(old.map((r) => r.position), Array.from({ length: 15 }, (_, i) => i + 1));
  assert.deepEqual(old.slice(0, 5).map((r) => r.result), ["Passed", "Warning", "Failed (critical)", "Not checked", "Passed"]);
  assert.deepEqual(old.slice(0, 5).map((r) => r.tone), ["ok", "warn", "crit", "gray", "ok"]);
  assert.ok(old.slice(5).every((r) => !r.recorded && r.result === "Not recorded for this run" && r.tone === "gray"));
  // A check the server knows and this list does not is kept at the end, as text (own keys only).
  const odd = checkList({ investigated: true, checks: [f("target_leakage", "pass", "info"), f("__proto__", "warning")] });
  assert.equal(odd.length, 16);
  assert.deepEqual([odd[15].check, odd[15].label, odd[15].result], ["__proto__", "  proto  ", "Warning"]);
});

test("banned words: no check name or result word uses internal words", () => {
  const banned = /\b(holdout|champion|digest|refs?|L[0-3]|jev|nooa|governance)\b/i;
  const rows = checkList({ investigated: true, checks: CHECK_ORDER.map((c) => f(c, "pass", "info")) });
  for (const row of rows) for (const text of [row.label, row.result]) assert.doesNotMatch(text, banned, text);
});

test("headline: 'All checks passed' only when all 15 were recorded and passed; a partial set is neutral and says what is missing", () => {
  const five = ["target_leakage", "overfit_gap", "duplicate_rows", "class_imbalance", "implausible_score"].map((c) => f(c, "pass", "info"));
  assert.deepEqual(passSummary({ investigated: true, checks: five }), {
    tone: "gray", pill: "None to review", sentence: "All 5 recorded checks passed; 10 were not recorded for this run.",
  });
  const all = CHECK_ORDER.map((c) => f(c, "pass", "info"));
  assert.deepEqual(passSummary({ investigated: true, checks: all }), { tone: "ok", pill: "All checks passed", sentence: "All 15 trust checks passed." });
  assert.deepEqual(passSummary({ investigated: true, checks: [...five.slice(0, 4), f("implausible_score", "warning")] }), { tone: "warn", pill: "1 to review", sentence: null });
  assert.deepEqual(passSummary({ investigated: true, checks: [f("target_leakage", "not_evaluated", "info")] }), { tone: "gray", pill: "None to review, 1 not checked", sentence: null });
  assert.equal(passSummary({ investigated: false, checks: [] }), null);
  assert.equal(passSummary(null), null);
});
