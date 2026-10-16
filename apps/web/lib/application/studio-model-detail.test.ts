import assert from "node:assert/strict";
import test from "node:test";
import {
  RECHECKED, THRESHOLD_NOT_RECORDED, VERSIONS_NOTE, atLockedThreshold, columnKind, columnsView, confusionView, contractError, fileChecksView, fileSize, finalTestView, knownLimits,
  savedFileView, versionRows, versionStatus, type FinalTestCardLike, type ModelVersionItemLike,
} from "./studio-model-detail.ts";

const P = "11111111-1111-4111-8111-111111111111";
const M1 = "22222222-2222-4222-8222-222222222222";
const M2 = "33333333-3333-4333-8333-333333333333";
const E = "44444444-4444-4444-8444-444444444444";
const when = (iso: string) => `at-${iso.slice(0, 10)}`;
const BANNED = /\b(release|champion|ref|digest|manifest|package|slot|tick|revision|holdout|worker|pickle|artifact|skops|sha-?256|idempotency)s?\b/i;
const item = (id: string, extra: Partial<ModelVersionItemLike> = {}): ModelVersionItemLike => ({
  id, version: "2", experiment_id: E, created_at: "2026-10-01T10:00:00Z", status: "not_in_use", approved_for_scoring: false, ...extra,
});

test("versions: newest first as sent, plain status pills and notes, CV numbers only, bad ids dropped", () => {
  const rows = versionRows(P, [
    item(M1, { status: "in_use", approved_for_scoring: true, selection_metric: "pr_auc", cv: { pr_auc: 0.70512, precision: 0.5 }, cv_at_locked_threshold: { precision: 0.631, recall: 0.8123 }, decision_threshold: 0.31 }),
    item(M2, { version: "1", status: "approved_earlier", last_approved_at: "2026-09-01T00:00:00Z", selection_metric: "roc_auc", cv: { roc_auc: 0.8, precision: 0.6, recall: 0.7 }, decision_threshold: 0.5 }),
    item("not-a-uuid", { status: "in_use" }),
  ], when);
  assert.equal(rows.length, 2);
  assert.equal(rows[0].model, "Model v2");
  assert.equal(rows[0].href, `/projects/${P}/models/${M1}`);
  assert.deepEqual(rows[0].status, { text: "In use", tone: "ok" });
  assert.equal(rows[0].note, "approved for scoring");
  assert.equal(rows[0].precision, "0.631", "at the locked threshold, not the 0.5 aggregate");
  assert.equal(rows[0].recall, "0.812");
  assert.match(rows[0].main, /0\.705$/);
  assert.equal(rows[1].status.text, "Approved earlier");
  assert.equal(rows[1].note, "last approved at-2026-09-01");
  assert.equal(rows[1].precision, "0.6", "default threshold: the CV aggregate already holds it");
  assert.doesNotMatch(VERSIONS_NOTE, BANNED);
  assert.match(VERSIONS_NOTE, /never the final test/);
});

test("versions: waiting for approval, in use without approval, unknown status and __proto__", () => {
  const rows = versionRows(P, [item(M1, { status: "waiting_for_approval" }), item(M2, { status: "in_use" })], when);
  assert.equal(rows[0].status.text, "Waiting for approval");
  assert.match(rows[0].note ?? "", /waits for a person in the Inbox/);
  assert.equal(rows[1].note, "no approval recorded");
  for (const key of ["__proto__", "constructor", "toString", "brand_new"]) assert.deepEqual(versionStatus(key), { text: "Standing not known", tone: "gray" });
  assert.equal(rows[0].main, "—");
});

test("a threshold metric with a tuned threshold and no fold mean is not guessed from the 0.5 aggregate", () => {
  assert.equal(atLockedThreshold(item(M1, { cv: { precision: 0.5 }, decision_threshold: 0.3 }), "precision"), null);
  assert.equal(atLockedThreshold(item(M1, { cv: { precision: 0.5 }, decision_threshold: null }), "precision"), THRESHOLD_NOT_RECORDED, "a missing threshold is never treated as 0.5");
  assert.equal(atLockedThreshold(item(M1, { cv: {}, decision_threshold: null }), "precision"), null);
  assert.equal(versionRows(P, [item(M1, { cv: { recall: 0.7 }, decision_threshold: null })], when)[0].recall, "threshold not recorded");
  assert.equal(atLockedThreshold(item(M1, { cv: {}, cv_at_locked_threshold: {} }), "__proto__"), null);
});

test("saved file: safe format with its parts and threshold, old format, none, unknown", () => {
  // A safe-format model: one file (it already holds the preparation steps), its column list kept beside it, and a
  // preparation part recorded too; the size is the one file's.
  const safe = savedFileView({ saved_format: "package_v1", artifacts: [{ role: "model", size_bytes: 4_404_019 }, { role: "preprocessor", size_bytes: 52_000 }, { role: "feature_manifest", size_bytes: 900 }], metrics: { decision_threshold: 0.31 } });
  assert.equal(safe.state, "saved");
  if (safe.state !== "saved") return;
  assert.equal(safe.size, "4.2 MB");
  assert.deepEqual(safe.includes, ["the trained model", "the steps that prepare each column", "the list of columns new data must have", "the threshold 0.31"]);
  assert.equal(safe.tone, "ok");
  // An old-format model: a pickled model and a separate preparation part, no column list file.
  const old = savedFileView({ saved_format: "legacy_quarantined", artifacts: [{ role: "model", size_bytes: 2048 }, { role: "preprocessor", size_bytes: 1024 }] });
  const odd = savedFileView({ saved_format: "legacy_quarantined", artifacts: [{ role: "model", size_bytes: 10 }, { role: "__proto__", size_bytes: 5 }] });
  assert.equal(odd.state === "saved" && odd.includes.length, 1, "an unknown part is ignored");
  assert.equal(old.state === "saved" && old.size, "3.0 KB");
  assert.equal(old.state === "saved" && old.includes.length, 2);
  assert.match(old.state === "saved" ? old.formatDetail : "", /Scoring with it, and approving it for scoring, are refused unless the server is set up to allow old models; train it again to use it normally\./);
  assert.equal(savedFileView({ saved_format: null }).state, "none");
  assert.equal(savedFileView({ saved_format: "zip_v9" }).state, "none");
  assert.equal(fileSize(10), "10 bytes");
  assert.equal(fileSize(Number.NaN), "—");
  for (const v of [safe, old]) assert.doesNotMatch(JSON.stringify(v), BANNED);
});

test("file checks: fixed sentences by code (never the server text), not recorded, not applicable, none, __proto__", () => {
  const view = fileChecksView({
    status: "verified", checked_at: "2026-10-01T10:00:00Z", rows_compared: 100,
    checks: [{ check: "opens", passed: true }, { check: "digests", passed: true }, { check: "same_predictions", passed: true }, { check: "__proto__", passed: true }, { check: "constructor", passed: false }],
  }, when);
  assert.equal(view.state, "verified");
  if (view.state !== "verified") return;
  assert.equal(view.items.length, 3);
  assert.equal(view.allPassed, true);
  assert.equal(view.items[2].text, "100 training rows score exactly the same with the saved file as with the trained model.");
  assert.equal(view.when, "at-2026-10-01");
  const failed = fileChecksView({ status: "verified", checks: [{ check: "same_predictions", passed: false }, { check: "digests", passed: false }] }, when);
  assert.equal(failed.state === "verified" && failed.allPassed, false);
  assert.match(failed.state === "verified" ? failed.items[0].text : "", /^Sample training rows score differently/);
  assert.match(fileChecksView({ status: "not_recorded" }, when).state, /not_recorded/);
  assert.equal(fileChecksView({ status: "not_applicable" }, when).state, "not_applicable");
  assert.equal(fileChecksView(null, when).state, "none");
  assert.equal(fileChecksView({ status: "verified", checks: [] }, when).state === "verified" && (fileChecksView({ status: "verified", checks: [] }, when) as { allPassed: boolean }).allPassed, false);
  for (const v of [view, failed, fileChecksView({ status: "not_recorded" }, when), fileChecksView({ status: "not_applicable" }, when), fileChecksView(null, when)]) {
    const text = v.state === "verified" ? v.items.map((i) => i.text).join(" ") : v.text;
    assert.doesNotMatch(text, BANNED);
  }
  assert.doesNotMatch(RECHECKED, BANNED);
});

test("columns new data must have: kinds, names only, notes, capped plain names", () => {
  assert.equal(columnKind({ name: "a", is_date: true, role: "numeric" }), "date");
  assert.equal(columnKind({ name: "a", role: "categorical" }), "category");
  assert.equal(columnKind({ name: "a", role: "numeric" }), "number");
  assert.equal(columnKind({ name: "a", training_dtype: "int64" }), "number");
  assert.equal(columnKind({ name: "a", training_dtype: "bool" }), "yes / no");
  assert.equal(columnKind({ name: "a", training_dtype: "datetime64[ns]" }), "date");
  assert.equal(columnKind({ name: "a", training_dtype: "object" }), "text");
  assert.equal(columnKind({ name: "a" }), "not recorded");
  const view = columnsView({
    status: "available", column_count: 3, target_column: "churn", unknown_columns: "ignored_and_listed", unseen_categories: "encoded_as_unknown",
    columns: [{ name: "plan‮<b>x</b>", role: "categorical", required: true }, { name: "fee", role: "numeric", required: false }, { name: "x".repeat(200), training_dtype: "float64" }],
  });
  assert.equal(view.count, 3);
  assert.equal(view.rows[0].name, "plan <b>x</b>", "bidi controls removed; the text is rendered as text, never markup");
  assert.equal(view.rows[1].required, "optional");
  assert.equal(view.rows[2].name.length, 80);
  assert.ok(view.notes.some((n) => n === "The column churn is what the model predicts; it is ignored if a file has it."));
  assert.ok(view.notes.some((n) => /Extra columns are fine/.test(n)));
  const legacy = columnsView({ status: "names_only", column_count: 2, columns: [{ name: "a" }, { name: "b" }] });
  assert.equal(legacy.namesOnly, true);
  assert.deepEqual(legacy.rows.map((r) => r.kind), ["not recorded", "not recorded"]);
  assert.match(legacy.notes[0], /only the names are listed/);
  for (const v of [view, legacy]) assert.doesNotMatch(v.notes.join(" "), BANNED);
});

test("column list refusals are fixed sentences, never the server message", () => {
  const err = (status: number, code = "", message = "SERVER WORDS manifest") => ({ status, body: { error: { code, message } } });
  assert.match(contractError(err(409, "feature_contract_unavailable")), /^The list of columns is not available/);
  assert.match(contractError(err(404, "not_found")), /not in the active workspace/);
  assert.match(contractError(err(500, "__proto__")), /could not be loaded/);
  assert.match(contractError(new Error("network")), /could not be loaded/);
  for (const s of [409, 404, 403, 500]) {
    const text = contractError(err(s));
    assert.doesNotMatch(text, /SERVER WORDS/);
    assert.doesNotMatch(text, BANNED);
  }
});

test("known limits: column count and unseen categories from the column list, chances only for yes/no models", () => {
  const binary = knownLimits({ taskType: "binary", contract: { status: "available", column_count: 29, unseen_categories: "encoded_as_unknown" } });
  assert.equal(binary.length, 4);
  assert.match(binary[0], /same 29 columns it was trained on/);
  assert.match(binary[1], /never seen category values/);
  assert.match(binary[2], /not exact chances/);
  const regression = knownLimits({ taskType: "regression", contract: null });
  assert.equal(regression.length, 2);
  assert.match(regression[0], /^New files must have the same columns it was trained on/);
  assert.ok(!regression.some((l) => /chances|category/.test(l)));
  const one = knownLimits({ taskType: "binary", contract: { status: "available", column_count: 1, unseen_categories: null } });
  assert.match(one[0], /same 1 column it/);
  for (const l of [...binary, ...regression]) assert.doesNotMatch(l, BANNED);
});

test("confusion: counts and sentences from the 2x2, nothing for absent or broken counts", () => {
  const view = confusionView({ tp: 1731, fp: 1017, fn: 406, tn: 5750, rows: 8904 }, 0.31);
  assert.ok(view);
  assert.equal(view?.tp, "1,731");
  assert.equal(view?.rows, "8,904");
  assert.deepEqual(view?.lines, [
    "It flagged 2,748 of 8,904 rows (31%) at the threshold 0.31.",
    "It caught 1,731 of the 2,137 real positives: recall 0.81.",
    "1,731 of the 2,748 flagged rows were real positives: precision 0.63.",
  ]);
  assert.equal(confusionView(null, 0.5), null, "withheld for connected tools: nothing drawn");
  assert.equal(confusionView(undefined, 0.5), null);
  assert.equal(confusionView({ tp: 1.5, fp: 0, fn: 0, tn: 1 }, 0.5), null);
  assert.equal(confusionView({ tp: -1, fp: 0, fn: 0, tn: 1 }, 0.5), null);
  assert.equal(confusionView({ tp: 0, fp: 0, fn: 0, tn: 0 }, 0.5), null);
  const none = confusionView({ tp: 0, fp: 0, fn: 0, tn: 10 }, null);
  assert.deepEqual(none?.lines, ["It flagged 0 of 10 rows (0%)."]);
});

const card = (final: Partial<FinalTestCardLike["final_evaluation"]>): FinalTestCardLike => ({
  cv: { metric: "pr_auc", mean: 0.7, folds: 5, at_locked_threshold: { precision: 0.61, recall: 0.82, f1: 0.7 } },
  split: { evaluation_rows: 9000, evaluation_split_strategy: "time_ordered" },
  final_evaluation: { status: "reported", metric: "pr_auc", value: 0.71, metrics: { pr_auc: 0.71, precision: 0.63, recall: 0.81, accuracy: 0.8, decision_threshold: 0.31 }, decision_threshold: 0.31, ...final },
});

test("final test beside cross-validation: the main score against the CV mean, threshold metrics at the locked threshold, rows, no dates", () => {
  const view = finalTestView(card({ confusion: { tp: 1731, fp: 1017, fn: 406, tn: 5750, rows: 8904 } }));
  assert.equal(view.state, "reported");
  if (view.state !== "reported") return;
  assert.deepEqual(view.compare.map((c) => [c.key, c.cv, c.final]), [["pr_auc", "0.7", "0.71"], ["precision", "0.61", "0.63"], ["recall", "0.82", "0.81"]]);
  assert.equal(view.rows, "8,904", "the 2x2 row count first");
  assert.equal(view.setAside, "time ordered");
  assert.ok(view.confusion);
  const noConfusion = finalTestView(card({ confusion: null }));
  assert.equal(noConfusion.state === "reported" && noConfusion.confusion, null);
  assert.equal(noConfusion.state === "reported" && noConfusion.rows, "9,000");
  const otherMetric = finalTestView({ ...card({}), cv: { metric: "roc_auc", mean: 0.9 } });
  assert.equal(otherMetric.state === "reported" && otherMetric.compare[0].cv, "—", "a different CV metric is never put beside it");
  const byPrecision = finalTestView({ ...card({ metric: "precision", value: 0.63 }), cv: { metric: "precision", mean: 0.5, at_locked_threshold: { precision: 0.61 } } });
  assert.deepEqual(byPrecision.state === "reported" && [byPrecision.compare[0].label, byPrecision.compare[0].cv], ["precision (at the locked threshold)", "0.61"], "a threshold-dependent main score is compared at the locked threshold");
  assert.equal(finalTestView(card({ status: "withheld" })).state, "withheld");
  assert.equal(finalTestView(card({ status: "missing" })).state, "missing");
  assert.equal(finalTestView(card({ value: null, metrics: {} })).state, "missing");
  assert.doesNotMatch(JSON.stringify(view), /\b20\d\d-\d\d/);
});
