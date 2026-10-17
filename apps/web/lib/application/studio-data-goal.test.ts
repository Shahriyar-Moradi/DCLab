import assert from "node:assert/strict";
import test from "node:test";
import {
  DEFAULT_WHY_MARK, DISCLOSED_LABEL, boundaryNote, NO_UPLOAD_CHECKS, SEALED_LABEL, TRUNCATED_NOTE, UNKNOWN_STATUS_SENTENCE, churnView, monthIndex, monthLabel, readErrorText,
  retentionText, sizeText, uploadCheckRows, uploadLogRows, utcText, whoDecidedRows, type TargetByPeriodLike,
} from "./studio-data-goal.ts";
import { uploaderText } from "./studio-data.ts";

const D1 = "11111111-1111-4111-8111-111111111111";
const DEC = "22222222-2222-4222-8222-222222222222";
const BANNED = /\b(holdout|hold-out|champion|digest|refs?|stale|releases?|manifest|package|slots?|ticks?|revision|lineage|candidates?|cursor|idempotency|failed|failure)\b/i;

const training = (month: string, rate: number, rows = 1000) => ({ month, part: "training", rows, rate });
const base = (extra: Partial<TargetByPeriodLike> = {}): TargetByPeriodLike => ({
  status: "computed", target_column: "churned_next_30d", time_column: "snapshot_month", training_rate: 0.2,
  buckets: [training("2025-01", 0.18), training("2025-02", 0.2), training("2025-03", 0.22)], truncated: false,
  final_test: { layout: "after_training", start_month: "2025-04", end_month: "2025-06", row_count: 8904, sealed: true, rate: null, disclosed_after_final_test: false },
  ...extra,
});

test("every status but computed gets its own plain sentence and no chart; unknown and __proto__ fall back", () => {
  const seen = new Set<string>();
  for (const status of ["no_split_plan", "not_binary", "no_time_column", "time_not_dates", "unavailable"]) {
    const view = churnView(base({ status }));
    assert.equal(view.kind, "sentence", status);
    if (view.kind !== "sentence") continue;
    assert.notEqual(view.text, UNKNOWN_STATUS_SENTENCE, status);
    assert.doesNotMatch(view.text, BANNED, view.text);
    assert.doesNotMatch(view.text, /\d+%/, "no rate in a status sentence");
    seen.add(view.text);
  }
  assert.equal(seen.size, 5, "one distinct sentence per status");
  for (const status of ["__proto__", "constructor", "toString", "weird"]) {
    const view = churnView(base({ status }));
    assert.deepEqual(view, { kind: "sentence", text: UNKNOWN_STATUS_SENTENCE });
  }
});

test("sealed after_training: a band with months and row count, no line, point or rate inside it", () => {
  const view = churnView(base());
  assert.equal(view.kind, "chart");
  if (view.kind !== "chart") return;
  assert.deepEqual(view.band, { start: monthIndex("2025-04"), end: monthIndex("2025-06"), rows: 8904 });
  assert.deepEqual(view.disclosed, []);
  assert.ok(view.training.every((p) => p.month < view.band!.start), "no training point inside the band");
  assert.equal(view.months.length, 6, "the axis spans training months and the band");
  const bandRow = view.table.find((r) => r.key === "band");
  assert.equal(bandRow?.part, SEALED_LABEL);
  assert.equal(bandRow?.rate, "sealed");
  assert.equal(bandRow?.rows, "8,904");
  assert.match(view.summary, /Final test — sealed until the final test runs \(8,904 rows\); no rate is shown/);
  assert.ok(view.notes.some((n) => /UTC/.test(n)));
});

test("a month cut by the split: its training rows are counted and a note says so", () => {
  const view = churnView(base({ buckets: [training("2025-02", 0.2), training("2025-03", 0.22), training("2025-04", 0.25, 120)] }));
  assert.ok(view.kind === "chart");
  if (view.kind !== "chart") return;
  assert.ok(view.notes.includes(boundaryNote(monthIndex("2025-04")!)));
  assert.ok(view.notes.includes("Apr 2025 has both training rows and final-test rows; only its training rows are counted."));
  const plain = churnView(base());
  assert.ok(plain.kind === "chart" && !plain.notes.some((n) => /both training rows/.test(n)));
});

test("sealed: final-test buckets and a final-test rate are ignored even if the read carried them", () => {
  const leaky = base({
    buckets: [training("2025-01", 0.18), training("2025-02", 0.2), { month: "2025-04", part: "final_test", rows: 3000, rate: 0.777 }],
    final_test: { layout: "after_training", start_month: "2025-04", end_month: "2025-06", row_count: 8904, sealed: true, rate: 0.777, disclosed_after_final_test: true },
  });
  const view = churnView(leaky);
  assert.equal(view.kind, "chart");
  if (view.kind !== "chart") return;
  assert.deepEqual(view.disclosed, []);
  const text = JSON.stringify(view);
  assert.doesNotMatch(text, /0\.777|77\.7|78%/, "no final-test rate anywhere in the model");
  assert.ok(view.band);
  // Half-disclosed (sealed false but not disclosed) is still sealed.
  const half = churnView(base({ buckets: leaky.buckets, final_test: { ...leaky.final_test!, sealed: false, disclosed_after_final_test: false } }));
  assert.equal(half.kind === "chart" && half.disclosed.length, 0);
  assert.doesNotMatch(JSON.stringify(half), /0\.777|78%/);
});

test("after disclosure: final-test months drawn apart and labelled, no band", () => {
  const view = churnView(base({
    buckets: [training("2025-01", 0.18), training("2025-02", 0.2), { month: "2025-04", part: "final_test", rows: 3000, rate: 0.24 }],
    final_test: { layout: "after_training", start_month: "2025-04", end_month: "2025-04", row_count: 3000, sealed: false, rate: 0.24, disclosed_after_final_test: true },
  }));
  assert.equal(view.kind, "chart");
  if (view.kind !== "chart") return;
  assert.equal(view.band, null);
  assert.equal(view.disclosed.length, 1);
  assert.equal(view.table.find((r) => r.key.startsWith("d"))?.part, DISCLOSED_LABEL);
  assert.match(view.summary, /Disclosed after the final test/);
  assert.ok(view.notes.some((n) => n.startsWith(`${DISCLOSED_LABEL}: 24%`)));
});

test("mixed layout: no band; final-test rows spread over all months and stay sealed", () => {
  const view = churnView(base({ final_test: { layout: "mixed", start_month: null, end_month: null, row_count: 900, sealed: true, rate: null, disclosed_after_final_test: false } }));
  assert.equal(view.kind, "chart");
  if (view.kind !== "chart") return;
  assert.equal(view.band, null);
  assert.ok(view.notes.some((n) => /spread over all months and stay sealed/.test(n)));
});

test("truncated, empty buckets, invalid months and the month helpers", () => {
  const view = churnView(base({ truncated: true }));
  assert.ok(view.kind === "chart" && view.notes.includes(TRUNCATED_NOTE));
  assert.equal(churnView(base({ buckets: [], final_test: null })).kind, "sentence");
  const odd = churnView(base({ buckets: [training("2025-13", 0.1), training("x", 0.1), training("2025-01", 2), training("2025-02", 0.3)] }));
  assert.ok(odd.kind === "chart" && odd.training.length === 1);
  assert.equal(monthIndex("2025-00"), null);
  assert.equal(monthLabel(monthIndex("2026-06")!), "Jun 2026");
  for (const v of [churnView(base()), view]) if (v.kind === "chart") for (const n of v.notes) assert.doesNotMatch(n, BANNED, n);
});

test("upload log: rejected rows keep the DCLab reason, file names are plain and capped, links only for accepted UUIDs", () => {
  const rows = uploadLogRows([
    { id: "a", created_at: "2026-10-01T00:00:00Z", file_name: "custom‮ers.xlsx", size_bytes: 2048, outcome: "rejected", reason_code: "unreadable_sheets", reason: "The spreadsheet could not be read.", uploaded_by: { kind: "person", is_you: true }, dataset_id: null, source: "v1_api" },
    { id: "b", created_at: "2026-10-02T00:00:00Z", file_name: "x".repeat(300), size_bytes: null, outcome: "accepted", uploaded_by: { kind: "connected_tool" }, dataset_id: D1, source: "labs" },
    { id: "c", created_at: "2026-10-03T00:00:00Z", file_name: "<img src=x onerror=alert(1)>", outcome: "rejected", reason_code: "too_large", reason: null, uploaded_by: { kind: "person" }, dataset_id: D1, source: "__proto__" },
    { id: "d", created_at: "2026-10-03T00:00:00Z", file_name: "a.csv", outcome: "rejected", reason_code: "__proto__", reason: "", uploaded_by: null, dataset_id: "javascript:alert(1)", source: "v1_api" },
    { id: "e", created_at: "2026-10-03T00:00:00Z", file_name: "b.csv", outcome: "accepted", uploaded_by: { kind: "person" }, dataset_id: "../../admin", source: "v1_api" },
  ], uploaderText);
  assert.equal(rows[0].reason, "The spreadsheet could not be read.");
  assert.equal(rows[0].fileName, "custom ers.xlsx", "bidi control removed");
  assert.equal(rows[0].by, "You");
  assert.equal(rows[0].outcomeText, "rejected");
  assert.equal(rows[1].fileName.length, 120);
  assert.equal(rows[1].datasetId, D1);
  assert.equal(rows[1].by, "A connected tool");
  assert.equal(rows[1].size, "Not known");
  assert.equal(rows[1].reason, null);
  assert.equal(rows[2].reason, "The file is larger than the upload limit.", "code fallback when no reason text");
  assert.equal(rows[2].datasetId, null, "a rejected row never links");
  assert.equal(rows[2].source, "Not recorded");
  assert.equal(rows[2].fileName, "<img src=x onerror=alert(1)>", "kept as text; React renders it as text");
  assert.equal(rows[3].reason, "No reason was recorded.");
  assert.equal(rows[3].by, "Unknown");
  assert.equal(rows[4].datasetId, null);
  assert.equal(sizeText(2048), "2.0 KB");
  assert.equal(sizeText(5 * 1024 ** 2), "5.0 MB");
  assert.equal(sizeText(-1), "Not known");
});

test("retention sentence from retention_days; nothing but metadata is claimed", () => {
  assert.equal(retentionText(90), "Upload attempts are kept for 90 days. Each one keeps the file name, size, outcome and reason, who uploaded it and when — never the file's contents or any row values.");
  assert.match(retentionText("90"), /for a limited time/);
  assert.match(retentionText(-3), /for a limited time/);
  assert.doesNotMatch(retentionText(90), BANNED);
});

test("checked on upload: ok/warn in plain words, warn never a failure, columns capped, __proto__ codes plain", () => {
  const rows = uploadCheckRows({
    ran_at: "2026-10-05T02:00:00Z", row_count: 48210,
    checks: [
      { code: "duplicate_rows", status: "warn", count: 14, share: 0.0003, columns: [] },
      { code: "missing_values", status: "warn", count: 2, share: 0.04, columns: [{ name: "contract_end", share: 0.38 }, { name: "churned_at", share: 0.79 }] },
      { code: "date_parsing", status: "ok", count: 0, share: null, columns: [] },
      { code: "id_like_columns", status: "warn", count: 9, share: null, columns: Array.from({ length: 8 }, (_, i) => ({ name: `id_${i}` })), columns_truncated: true },
      { code: "constant_columns", status: "warn", count: 1, share: null, columns: [{ name: "x\u0000y" }] },
      { code: "__proto__", status: "maybe", columns: [] },
    ],
  });
  assert.deepEqual(rows.map((r) => r.word), ["worth a look", "worth a look", "ok", "worth a look", "worth a look", "not known"]);
  assert.equal(rows[0].text, "14 rows are exact copies of another row (<0.1% of rows).");
  assert.match(rows[1].text, /^4\.0% of all cells are empty\. Most empty: contract_end \(38% empty\), churned_at \(79% empty\)\.$/);
  assert.equal(rows[2].label, "Dates");
  assert.match(rows[3].text, /id_0, id_1, id_2, id_3, id_4, id_5 and 3 more\.$/);
  assert.match(rows[4].text, /^1 column has one value or none: x y\.$/);
  assert.equal(rows[5].label, "proto");
  for (const r of rows) assert.doesNotMatch(`${r.word} ${r.text}`, BANNED, r.text);
  assert.doesNotMatch(NO_UPLOAD_CHECKS, BANNED);
  assert.equal(utcText("2026-10-05T02:00:00Z"), "5 Oct 2026, 02:00 UTC");
  assert.equal(utcText("nope"), "—");
});

test("who decided: plain labels, null meanings, default reason marked, links only for UUIDs, __proto__ safe", () => {
  const rows = whoDecidedRows([
    { what: "rule", suggested_by: "person", confirmed_by: "you", when: "2026-10-01T10:00:00Z", why: "Retention can reach 80%", why_is_default: false, value: null, decision_id: DEC },
    { what: "test_design", suggested_by: "rules", confirmed_by: null, when: null, why: null, why_is_default: false, value: "time-ordered", decision_id: null },
    { what: "target", suggested_by: "assistant", confirmed_by: "connected_tool", when: "2026-10-01T09:00:00Z", why: "Confirmed in the run setup.", why_is_default: true, value: "churned\u0007_next_30d", decision_id: "javascript:alert(1)" },
    { what: "metric", suggested_by: null, confirmed_by: null, when: null, why: null, why_is_default: true, value: "pr_auc", decision_id: null },
  ]);
  assert.deepEqual(rows.map((r) => r.what), ["What we predict", "Test design", "Ranking metric", "Business rule"]);
  assert.deepEqual(rows.map((r) => r.suggested), ["The assistant", "The rules", "Not recorded", "A person"]);
  assert.deepEqual(rows.map((r) => r.confirmed), ["A connected tool", "Nobody was asked", "Not recorded", "You"]);
  assert.equal(rows[0].why, `Confirmed in the run setup. ${DEFAULT_WHY_MARK}`);
  assert.equal(rows[0].value, "churned _next_30d");
  assert.equal(rows[0].decisionId, null);
  assert.equal(rows[2].why, DEFAULT_WHY_MARK);
  assert.equal(rows[1].why, "No reason recorded");
  assert.equal(rows[1].when, "Not recorded");
  assert.equal(rows[3].decisionId, DEC);
  assert.equal(rows[3].value, "—");
  assert.equal(rows[1].value, "Time-ordered", "test-design code in words");
  assert.equal(rows[2].value, "PR-AUC", "metric code in words");
  const values = whoDecidedRows([
    { what: "test_design", suggested_by: "rules", confirmed_by: "you", value: "keep_rule_split", why_is_default: true },
    { what: "test_design", suggested_by: "person", confirmed_by: null, value: "odd\u202ecode", why_is_default: false },
    { what: "metric", suggested_by: "rules", confirmed_by: null, value: "my_metric", why_is_default: false },
    { what: "target", suggested_by: "rules", confirmed_by: null, value: "churn", why_is_default: false },
  ]);
  assert.deepEqual(values.map((r) => r.value), ["churn", "Kept the rules' test design", "odd code", "my metric"]);
  assert.deepEqual(values.map((r) => r.confirmed), ["Not recorded", "You", "Not recorded", "Not recorded"], "only the rules' test design is 'Nobody was asked'");
  assert.equal(rows[3].when, "1 Oct 2026, 10:00 UTC");
  const odd = whoDecidedRows([{ what: "__proto__", suggested_by: "__proto__", confirmed_by: "constructor", why_is_default: false }]);
  assert.equal(odd[0].suggested, "Not recorded");
  assert.equal(odd[0].confirmed, "Not recorded");
  assert.equal(odd[0].what, "proto");
  for (const r of rows) assert.doesNotMatch(`${r.suggested} ${r.confirmed} ${r.why}`, BANNED);
});

test("read errors: fixed sentences by code, then status; never the server's message", () => {
  const server = "SELECT * FROM secrets failed at line 3";
  assert.match(readErrorText({ status: 400, body: { error: { code: "invalid_cursor", message: server } } }, "the upload log"), /could not continue/);
  assert.equal(readErrorText({ status: 400, body: { error: { code: "validation_error" } } }, "x"), "Could not load x. Reload the page to try again.", "a 400 is the cursor only by its code");
  assert.match(readErrorText({ status: 404, body: { error: { code: "not_found", message: server } } }, "x"), /not in the active workspace/);
  assert.match(readErrorText({ status: 403 }, "x"), /Your role cannot read/);
  assert.match(readErrorText({ status: 429 }, "x"), /Too many requests/);
  assert.match(readErrorText({ status: 401 }, "x"), /Sign in again/);
  assert.equal(readErrorText({ status: 500, message: server }, "who decided"), "Could not load who decided. Reload the page to try again.");
  assert.equal(readErrorText({ status: 500, body: { error: { code: "__proto__" } } }, "x"), "Could not load x. Reload the page to try again.");
  assert.equal(readErrorText({ status: "constructor" }, "x"), "Could not load x. Reload the page to try again.");
  assert.equal(readErrorText(null, "x"), "Could not load x. Reload the page to try again.");
  for (const e of [{ status: 400 }, { status: 404 }, { status: 500, message: server }]) assert.ok(!readErrorText(e, "x").includes("secrets"));
});
