import assert from "node:assert/strict";
import test from "node:test";
import {
  FINAL_EVAL_HEADING, baselineView, cardFilename, cardNumber, cardTitle, dataRows, driversView, finalView, findingsHref, llmLine, markdownDownload, thresholdSourceText, modelRows, risksView, splitRows,
} from "./studio-card.ts";

const P = "11111111-1111-4111-8111-111111111111";
const E = "22222222-2222-4222-8222-222222222222";
const M = "abcdef12-3333-4333-8333-333333333333";

const drivers = (status = "computed") => ({
  status, text: "tenure drives the model.",
  features: [
    { rank: 2, column: "plan", importance_mean: 0.02, importance_std: 0.01, distinguishable: false },
    { rank: 1, column: "tenure", importance_mean: 0.08, importance_std: 0.004, distinguishable: true },
  ],
  clear_drivers: ["tenure"],
});

test("drivers: sorted by rank, widths relative to the largest, stored importance only", () => {
  const view = driversView(drivers());
  assert.equal(view.state, "bars");
  if (view.state !== "bars") return;
  assert.deepEqual(view.bars.map((b) => [b.rank, b.column, b.width]), [[1, "tenure", 100], [2, "plan", 25]]);
  assert.equal(view.clearCount, 1);
  assert.equal(view.bars[0].clear, true);
});

test("drivers degrade to an explicit empty state (not computed, skipped, no features)", () => {
  for (const status of ["not_computed", "skipped", "not_applicable"]) {
    const view = driversView({ status, text: "n/a", features: [] });
    assert.equal(view.state, "empty");
  }
  assert.equal(driversView({ status: "computed", text: "x", features: [] }).state, "empty");
  const nonPositive = driversView({ status: "computed", text: "x", features: [{ rank: 1, column: "a", importance_mean: -0.1 }] });
  assert.equal(nonPositive.state === "bars" && nonPositive.bars[0].width, 0);
});

test("untrusted column names stay plain text", () => {
  const view = driversView({ status: "computed", text: "t", features: [{ rank: 1, column: "<img src=x onerror=1>\u0007", importance_mean: 1 }] });
  assert.equal(view.state === "bars" && view.bars[0].column, "<img src=x onerror=1>");
});

test("baseline: clear win, small win, loss, unavailable", () => {
  const base = { available: true, text: "t", metric: "roc_auc", baseline_score: 0.5, winner_score: 0.8, margin: 0.3 };
  assert.equal(baselineView({ ...base, beats_baseline: true, clear_margin: true }).tone, "ok");
  assert.match(baselineView({ ...base, beats_baseline: true, clear_margin: false }).badge, /small margin/);
  assert.match(baselineView({ ...base, beats_baseline: false }).badge, /Does not beat/);
  const none = baselineView({ available: false, text: "no baseline" });
  assert.deepEqual([none.tone, none.rows.length], ["gray", 0]);
  assert.equal(baselineView({ ...base, beats_baseline: true, clear_margin: true }).rows[0].value, "0.8");
});

test("final evaluation renders only when the API reports it, labelled, as a single look", () => {
  const binary = finalView({ status: "reported", metric: "roc_auc", value: 0.81234, metrics: { roc_auc: 0.81234, f1: 0.7, recall: 0.65, decision_threshold: 0.42 }, decision_threshold: 0.42 });
  assert.equal(binary.state, "reported");
  if (binary.state === "reported") {
    assert.equal(binary.metric, "roc auc");
    assert.equal(binary.value, "0.812");
    assert.deepEqual(binary.others.map((o) => o.key), ["f1", "recall"]);
    assert.equal(binary.threshold, "0.42");
  }
  const regression = finalView({ status: "reported", metric: "rmse", value: 12.5, metrics: { rmse: 12.5, mae: 9.1 } });
  assert.equal(regression.state === "reported" && regression.threshold, null);
  const multi = finalView({ status: "reported", metric: "macro_f1", value: 0.7, metrics: { macro_f1: 0.7, balanced_accuracy: 0.69 } });
  assert.equal(multi.state === "reported" && multi.others.length, 1);
  const withheld = finalView({ status: "withheld", metrics: {}, value: null });
  assert.equal(withheld.state, "withheld");
  assert.ok(!("value" in withheld));
  assert.equal(finalView({ status: "withheld", metrics: { roc_auc: 0.9 }, value: 0.9 }).state, "withheld");
  assert.equal(finalView({ status: "pending", metrics: { roc_auc: 0.9 }, value: 0.9 }).state, "missing");
  assert.equal(finalView({ status: "missing", metrics: { roc_auc: 0.9 }, value: 0.9 }).state, "missing");
  assert.equal(finalView({ status: "missing", note: "No evaluation stored." }).state, "missing");
  // "reported" without any number must not render an empty-looking result.
  assert.equal(finalView({ status: "reported", metrics: {}, value: null }).state, "missing");
  assert.match(FINAL_EVAL_HEADING, /one look at rows the model never trained on or was selected with/);
});

test("risks: worst first, counts need-attention, not-investigated and clean states", () => {
  const items = [
    { check: "class_imbalance", status: "pass", severity: "info", message: "ok" },
    { check: "target_leakage", status: "fail", severity: "critical", message: "plan looks like the answer" },
    { check: "overfit_gap", status: "warning", severity: "warning", message: "gap" },
  ];
  const view = risksView({ investigated: true, text: "t", items });
  assert.equal(view.state, "attention");
  assert.equal(view.attention, 2);
  assert.deepEqual(view.items.map((i) => i.check), ["target_leakage", "overfit_gap", "class_imbalance"]);
  assert.equal(risksView({ investigated: true, text: "t", items: [items[0]] }).state, "clean");
  assert.equal(risksView({ investigated: false, text: "t", items: [] }).state, "none_run");
});

test("findings link needs both ids to be UUIDs", () => {
  assert.equal(findingsHref(P, E), `/projects/${P}/experiments/${E}#findings`);
  assert.equal(findingsHref("x", E), null);
  assert.equal(findingsHref(P, "../x"), null);
});

test("data and split rows are counts only and skip missing values", () => {
  assert.deepEqual(dataRows({ name: "churn.csv", row_count: 240, column_count: 6 }).map((r) => [r.label, r.value]), [["Source dataset", "churn.csv"], ["Rows", "240"], ["Columns", "6"]]);
  const rows = splitRows({ train_rows: 192, evaluation_rows: 48, evaluation_split_strategy: "random_stratified", evaluation_fraction: 0.2, validation_strategy: "stratified_kfold", validation_folds: 5, stratified: true });
  assert.deepEqual(rows.map((r) => r.key), ["train", "eval", "strategy", "fraction", "validation", "strat"]);
  assert.equal(rows.find((r) => r.key === "fraction")?.value, "20.0%");
  assert.deepEqual(splitRows({}), []);
});

test("LLM line and title", () => {
  assert.equal(llmLine({ used: false, purposes: [] }), "LLM used: no");
  assert.equal(llmLine({ used: true, purposes: ["plan_advice"] }), "LLM used: yes (plan advice)");
  assert.equal(cardTitle({ family: "logistic_regression", version: "v1" }), "Model card: logistic_regression v1");
});

test("markdown download: short-id filename, plain text, none when empty or id invalid", () => {
  assert.equal(cardFilename(M), "model-card-abcdef12.md");
  assert.equal(cardFilename("nope"), null);
  assert.deepEqual(markdownDownload({ markdown: "# Model card: x" }), { text: "# Model card: x", type: "text/markdown;charset=utf-8" });
  assert.equal(markdownDownload({ markdown: "  " }), null);
  assert.equal(markdownDownload({}), null);
});

test("threshold source wording", () => {
  assert.equal(thresholdSourceText("default"), "(default, not tuned)");
  assert.equal(thresholdSourceText("cv_oof"), "(chosen on cross-validation)");
});

test("clear count counts shown bars, total reported separately", () => {
  const many = driversView({ status: "computed", text: "t", features: [{ rank: 1, column: "a", importance_mean: 1, distinguishable: true }], clear_drivers: ["a", "b", "c"] });
  assert.deepEqual(many.state === "bars" && [many.clearCount, many.clearTotal], [1, 3]);
});

test("cardNumber", () => {
  assert.deepEqual([cardNumber(0.81234), cardNumber(1234), cardNumber(123.456), cardNumber(null), cardNumber(NaN)], ["0.812", "1,234", "123.5", "n/a", "n/a"]);
});

test("models list: champion first then newest, UUID ids only, links to the card", () => {
  const node = (id: string, created: string, refs: string[] = [], kind = "model_version") => ({ kind, id, label: `m ${id.slice(0, 4)}`, version: "v1", created_at: created, ref_kinds: refs });
  const A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  const B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
  const rows = modelRows(P, [node(A, "2026-01-01"), node(B, "2026-02-01"), node(M, "2026-03-01", ["champion_model"]), node(E, "2026-04-01", [], "experiment"), node("bad", "2026-05-01")]);
  assert.deepEqual(rows.map((r) => r.id), [M, B, A]);
  assert.equal(rows[0].champion, true);
  assert.equal(rows[0].href, `/projects/${P}/models/${M}`);
});
