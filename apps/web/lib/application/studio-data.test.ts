import assert from "node:assert/strict";
import test from "node:test";
import {
  assistantVsRules, columnUse, columnWhy, leakageExcluded, compareRoles, dataCheckRows, fileSummary, filterColumns, isLeakageLeftOut, percent, periodText, pickDatasetId,
  preparedIds, recordedReason, relativeTime, roleLabel, typeLabel, uploadErrorText, uploaderText, usedBy, usedByText, versionStatus,
} from "./studio-data.ts";

const D1 = "11111111-1111-4111-8111-111111111111";
const D2 = "22222222-2222-4222-8222-222222222222";

test("usedBy counts split plans, runs and models of one version, once each, without attribute edges", () => {
  const edges = [
    { from: { kind: "split_plan", id: "sp1" }, to: { kind: "dataset_version", id: D1 }, relation: "partitions" },
    { from: { kind: "experiment", id: "e1" }, to: { kind: "dataset_version", id: D1 }, relation: "uses_dataset" },
    { from: { kind: "experiment", id: "e1" }, to: { kind: "dataset_version", id: D1 }, relation: "uses_dataset" },
    { from: { kind: "experiment", id: "e2" }, to: { kind: "dataset_version", id: D1 }, relation: "prepared_as", attribute: true },
    { from: { kind: "model_version", id: "m1" }, to: { kind: "dataset_version", id: D1 }, relation: "uses_dataset" },
    { from: { kind: "experiment", id: "e3" }, to: { kind: "dataset_version", id: D2 }, relation: "uses_dataset" },
  ];
  assert.deepEqual(usedBy(edges, D1), { split_plan: 1, experiment: 1, model_version: 1 });
  assert.equal(usedByText(usedBy(edges, D1)), "1 test design · 1 run · 1 model");
  assert.equal(usedByText(usedBy([], D1)), "Not used yet");
  assert.deepEqual([...preparedIds(edges)], [D1]);
});

test("pickDatasetId prefers the URL, then the dataset ref, then the newest", () => {
  assert.equal(pickDatasetId([D1, D2], D2, D1), D2);
  assert.equal(pickDatasetId([D1, D2], "not-in-project", D2), D2);
  assert.equal(pickDatasetId([D1, D2], null, "other"), D1);
  assert.equal(pickDatasetId([], null, null), null);
});

test("percent, roleLabel and compareRoles read the API fields plainly", () => {
  assert.equal(percent(null), "—");
  assert.equal(percent(0), "0%");
  assert.equal(percent(0.0123), "1.2%");
  assert.equal(percent(0.349), "35%");
  assert.equal(roleLabel("ignored_free_text"), "ignored (free text)");
  assert.equal(roleLabel(null), "—");
  assert.equal(compareRoles("numerical", "numerical"), "same");
  assert.equal(compareRoles("numerical", "categorical"), "differs");
  assert.equal(compareRoles("numerical", null), "no_run");
  assert.equal(compareRoles(null, "numerical"), "no_rule");
});

const col = (over: Record<string, unknown>) => ({ name: "c", physical_dtype: "float64", rule_role: "numerical", role_used: "numerical", missing_fraction: 0, unique_count: 50, leakage_excluded: false, ...over });

test("columnUse gives a plain reason from the roles and the leakage plan, and nothing when unknown", () => {
  assert.deepEqual(columnUse(col({ role_used: "target", rule_role: "target" })), { used: "target", reason: "This is what we predict" });
  assert.equal(columnUse(col({ role_used: "identifier", rule_role: "identifier" })).reason, "ID column, not a predictor");
  assert.equal(columnUse(col({ leakage_excluded: true })).used, "no");
  assert.match(columnUse(col({ leakage_excluded: true })).reason ?? "", /give away the answer/);
  assert.equal(columnUse(col({ leakage_excluded: true, role_used: "identifier" })).reason, "ID column, not a predictor");
  assert.equal(columnUse(col({ leakage_excluded: true, leakage_risk: "none" })).reason, "Left out by the plan: not a predictor");
  assert.equal(columnUse(col({ role_used: "ignored_free_text", rule_role: "ignored_free_text" })).used, "no");
  assert.equal(columnUse(col({ role_used: "ignored_free_text", role_source: "branch_change_set" })).reason, "Left out by a change you made");
  assert.equal(columnUse(col({ unique_count: 1 })).used, "unknown");
  assert.equal(columnUse(col({})).used, "yes");
  assert.deepEqual(columnUse(col({ role_used: null, rule_role: null })), { used: "unknown", reason: null });
  // No finished run: only the target and ID columns are stated; the rule role ignores the leakage plan.
  assert.equal(columnUse(col({ role_used: null }), false).used, "unknown");
  assert.equal(columnUse(col({ role_used: null, rule_role: "identifier" }), false).used, "no");
  assert.equal(columnUse(col({ role_used: null, rule_role: "target" }), false).used, "target");
  // Prototype keys are not roles.
  assert.equal(columnUse(col({ role_used: "__proto__", rule_role: "__proto__" })).reason, null);
  assert.equal(columnUse(col({ role_used: "constructor", rule_role: "constructor" })).reason, null);
});

test("leakageExcluded leaves out ID, entity and risk-free columns", () => {
  const cols = [col({ name: "a", leakage_excluded: true }), col({ name: "id", leakage_excluded: true, role_used: "identifier" }), col({ name: "g", leakage_excluded: true, leakage_risk: "none" }), col({ name: "k" })];
  assert.deepEqual(leakageExcluded(cols).map((c) => c.name), ["a"]);
});

test("typeLabel prefers the role, then the stored type name", () => {
  assert.equal(typeLabel("object", "categorical"), "category");
  assert.equal(typeLabel("int64"), "number");
  assert.equal(typeLabel("datetime64[ns]"), "date");
  assert.equal(typeLabel("object"), "text");
  assert.equal(typeLabel("weird"), "weird");
});

test("fileSummary shows only what was returned; entity, period and positive share are labelled training period", () => {
  const dataset = { row_count: 48210, column_count: 31 };
  assert.deepEqual(fileSummary(dataset, null).map((i) => [i.key, i.label]), [["rows", "rows"], ["cols", "columns"]]);
  const noSplit = { statistics_status: "no_split_plan", split_plan: null, experiment: null, columns: [], entity_count: 5, entity_column: "id", target_rate: 0.2 };
  assert.deepEqual(fileSummary(dataset, noSplit).map((i) => i.key), ["rows", "cols"]);
  const computed = {
    statistics_status: "computed", split_plan: { training_row_count: 39306, target_column: "churned" }, experiment: {},
    columns: [col({ name: "a" }), col({ name: "id", role_used: "identifier", rule_role: "identifier" }), col({ name: "churned", role_used: "target" })],
    entity_column: "customer_id", entity_count: 9412, time_range: { column: "month", start: "2025-01-01T00:00:00Z", end: "2026-03-31T00:00:00Z" }, target_rate: 0.213,
  };
  const items = fileSummary(dataset, computed, (v) => v, "customer-month");
  assert.deepEqual(items.map((i) => i.key), ["rows", "cols", "entity", "rate"]);
  assert.deepEqual([items[0].label, items[0].hint], ["rows (one per customer-month)", "39,306 in the training period"]);
  assert.equal(items[1].label, "columns · 1 used, 1 left out");
  assert.deepEqual([items[2].value, items[2].label], ["9,412", "different customer_id values · training period, Jan 2025 – Mar 2026"]);
  assert.deepEqual([items[3].value, items[3].label], ["21%", "of rows: churned is the answer the model treats as yes · training period"]);
  const periodOnly = fileSummary(dataset, { ...computed, entity_column: null, entity_count: null, target_rate: null });
  assert.deepEqual(periodOnly.slice(2).map((i) => [i.value, i.label]), [["Jan 2025 – Mar 2026", "training period, by month"]]);
  assert.equal(periodText("2026-06-01T00:00:00Z", "2026-06-20T00:00:00Z"), "1 Jun 2026 – 20 Jun 2026");
  assert.equal(periodText("bad", "2026-01-01"), null);
  assert.equal(periodText("2026-02-01", "2026-01-01"), null);
});

test("columns: filter by name, red leakage, the recorded reason as 'Why', assistant vs rules hidden while AI is off", () => {
  const cols = [col({ name: "churned_at", leakage_excluded: true, leakage_risk: "high", leakage_reason: "Filled in only after the customer cancelled" }), col({ name: "Plan_Type", role_reason: "6 categories" }), col({ name: "__proto__" })];
  assert.deepEqual(filterColumns(cols, " plan ").map((c) => c.name), ["Plan_Type"]);
  assert.equal(filterColumns(cols, "").length, 3);
  assert.equal(isLeakageLeftOut(cols[0]), true);
  assert.equal(isLeakageLeftOut(col({ leakage_excluded: true, role_used: "identifier" })), false);
  assert.deepEqual(columnWhy(cols[0], true), { text: "Left out by the leakage check: it may give away the answer", recorded: "Filled in only after the customer cancelled" });
  // The role reason is engine wording: technical details only, unless there is no plain reason.
  assert.deepEqual(columnWhy(cols[1], true), { text: "A number the model can use", recorded: null });
  assert.equal(recordedReason(cols[1], true), "6 categories");
  assert.equal(recordedReason(cols[0], true), "Filled in only after the customer cancelled");
  assert.equal(recordedReason(cols[1], false), null);
  assert.deepEqual(columnWhy(col({ role_used: null, rule_role: null, role_reason: "x" }), true), { text: "x", recorded: null });
  assert.equal(columnWhy(cols[0], false).recorded, null);
  const E = "e1";
  const rec = (columns: unknown[], over: Record<string, unknown> = {}) => ({ decision_type: "decision_point_resolved", effective_state: "accepted", subject: { kind: "experiment", id: E }, details: { decision_point: "column.semantic_role", columns }, ...over });
  // AI off: every answer is "off", so the column is hidden.
  assert.equal(assistantVsRules([rec([{ column: "a", agreement: "off", rule: "numerical", ai: null, source: "rule" }])], E, false), null);
  assert.equal(assistantVsRules([], E, false), null);
  assert.equal(assistantVsRules([rec([{ column: "a", agreement: "agree" }])], null, false), null);
  // Hidden unless AI is known to be on now, even when an older record has assistant answers.
  assert.equal(assistantVsRules([rec([{ column: "a", agreement: "agree" }])], E, true), null);
  assert.equal(assistantVsRules([rec([{ column: "a", agreement: "agree" }])], E, null), null);
  const map = assistantVsRules([
    rec([{ column: "region", agreement: "disagree", rule: "categorical", ai: "ignored_free_text", source: "human_pending" }, { column: "__proto__", agreement: "agree", rule: true, ai: true, source: "rule" }]),
    rec([{ column: "region", agreement: "agree", rule: false, ai: false }], { details: { decision_point: "column.is_identifier", columns: [{ column: "region", agreement: "agree", rule: false, ai: false }] } }),
    rec([{ column: "fee", agreement: "disagree", rule: "numerical", ai: "categorical", source: "rule" }]),
    rec([{ column: "other", agreement: "agree" }], { subject: { kind: "experiment", id: "another-run" } }),
    rec([{ column: "old", agreement: "agree" }], { effective_state: "superseded" }),
  ], E, false);
  assert.ok(map);
  assert.deepEqual([...map.keys()].sort(), ["__proto__", "fee", "region"]);
  assert.equal(map.get("region")?.pill, "they disagreed: we asked you");
  assert.equal(map.get("region")?.text, "Kind of column: assistant ignored free text, rules categorical. ID column: assistant no, rules no.");
  assert.equal(map.get("fee")?.pill, "they disagreed: the rules' answer was used");
  assert.equal(map.get("__proto__")?.pill, "both agreed");
  assert.equal(({} as Record<string, unknown>).polluted, undefined);
});

test("versions: relative time, who uploaded, in use / newer / made by a run", () => {
  const now = Date.parse("2026-10-16T12:00:00Z");
  assert.equal(relativeTime("2026-10-16T11:59:40Z", now), "just now");
  assert.equal(relativeTime("2026-10-16T11:00:00Z", now), "1 hour ago");
  assert.equal(relativeTime("2026-10-13T12:00:00Z", now), "3 days ago");
  assert.equal(relativeTime("2026-07-24T12:00:00Z", now), "12 weeks ago");
  assert.equal(relativeTime("2024-10-01T12:00:00Z", now), "2 years ago");
  assert.equal(relativeTime("nope", now), "—");
  assert.equal(uploaderText({ kind: "person", is_you: true }), "You");
  assert.equal(uploaderText({ kind: "person" }), "A person");
  assert.equal(uploaderText({ kind: "unknown" }), "Unknown");
  assert.equal(uploaderText({ kind: "connected_tool" }), "A connected tool");
  assert.equal(uploaderText({ kind: "__proto__" }), "Unknown");
  assert.equal(uploaderText(undefined), "Unknown");
  const inUse = { id: D1, created_at: "2026-06-01T00:00:00Z" };
  const used = { split_plan: 1, experiment: 2, model_version: 1 };
  assert.deepEqual(versionStatus(inUse, { inUse, prepared: false, used }), { inUse: true, newer: false, text: "The data in use. Used by 1 test design · 2 runs · 1 model." });
  assert.deepEqual(versionStatus({ id: D2, created_at: "2026-09-01T00:00:00Z" }, { inUse, prepared: false, used: { split_plan: 0, experiment: 0, model_version: 0 } }), { inUse: false, newer: true, text: "Newer than the data in use. Not used yet." });
  assert.equal(versionStatus({ id: D2, created_at: "2026-09-01T00:00:00Z" }, { inUse, prepared: true, used: null }).newer, false);
  assert.equal(versionStatus({ id: D2, created_at: "2026-09-01T00:00:00Z" }, { inUse, prepared: true, used: { split_plan: 0, experiment: 0, model_version: 0 } }).text, "Made by a run from an uploaded file.");
  assert.equal(versionStatus({ id: D2, created_at: "2026-01-01T00:00:00Z" }, { inUse: null, prepared: false, used: null }).text, "—");
});

test("upload errors are fixed plain sentences keyed by code, never the server's message", () => {
  const err = (status: number, code?: string, message = "secret server text /tmp/x") => ({ status, body: { error: { code, message } } });
  assert.match(uploadErrorText(err(422, "upload_rejected")).detail, /^Nothing was added\. Use a CSV/);
  assert.equal(uploadErrorText(err(413)).title, "The file is too large");
  assert.equal(uploadErrorText(err(429, "quota_exceeded")).title, "Your workspace has no storage left");
  assert.equal(uploadErrorText(err(403, "forbidden")).title, "You cannot upload here");
  assert.equal(uploadErrorText(err(422, "__proto__")).title, "The upload was refused");
  assert.equal(uploadErrorText(err(500, "constructor")).title, "Could not confirm the upload");
  assert.equal(uploadErrorText(new Error("boom")).title, "Could not confirm the upload");
  for (const status of [400, 403, 404, 413, 422, 429, 500]) assert.doesNotMatch(JSON.stringify(uploadErrorText(err(status))), /secret|\/tmp/);
});

const BANNED = /\b(holdout|hold-out|champion|digest|refs?|stale|releases?|manifest|package|slots?|ticks?|revision|lineage|candidates?)\b/i;
test("every new Data sentence passes the banned-word list", () => {
  const sentences = [
    ...["upload_rejected", "payload_too_large", "length_required", "quota_exceeded", "idempotency_key_conflict", "validation_failed"].flatMap((code) => Object.values(uploadErrorText({ status: 422, body: { error: { code } } }))),
    ...[400, 403, 404, 429, 500].flatMap((status) => Object.values(uploadErrorText({ status }))),
    ...["person", "connected_tool", "agent", "rule", "unknown"].map((kind) => uploaderText({ kind })),
    versionStatus({ id: D1, created_at: "2026-01-01" }, { inUse: { id: D1, created_at: "2026-01-01" }, prepared: false, used: { split_plan: 1, experiment: 1, model_version: 1 } }).text,
    versionStatus({ id: D2, created_at: "2026-01-01" }, { inUse: null, prepared: true, used: null }).text,
    ...["they disagreed: we asked you", "both agreed"],
    ...fileSummary({ row_count: 1, column_count: 1 }, { statistics_status: "computed", split_plan: { training_row_count: 1, target_column: "y" }, experiment: {}, columns: [], entity_column: "e", entity_count: 1, target_rate: 0.5 }).flatMap((i) => [i.label, i.hint ?? ""]),
  ];
  for (const text of sentences) assert.doesNotMatch(text, BANNED, text);
});

test("dataCheckRows are plain, capped and say 'not checked yet' instead of guessing", () => {
  const none = { statistics_status: "no_split_plan", split_plan: null, experiment: null, columns: [col({ missing_fraction: null })] };
  assert.deepEqual(dataCheckRows(none, null).map((r) => r.result), ["unknown", "unknown", "unknown"]);
  assert.match(dataCheckRows({ ...none, statistics_status: "unavailable" }, null)[1].text, /Withheld/);
  const computed = {
    statistics_status: "computed", split_plan: { training_row_count: 10, target_column: "y" }, experiment: {},
    columns: [col({ name: "contract_end", missing_fraction: 0.38 }), col({ name: "churned_at", missing_fraction: 0.79, leakage_excluded: true }), col({ name: "id", leakage_excluded: true, role_used: "identifier" }), col({ name: "ok", missing_fraction: 0 })],
  };
  const rows = dataCheckRows(computed, [{ check: "duplicate_rows", status: "pass", message: "No duplicates." }, { check: "target_leakage", status: "pass", message: "No leakage found." }]);
  assert.equal(rows[0].result, "ok");
  assert.match(rows[1].text, /churned_at is empty for 79% of rows; contract_end is empty for 38% of rows/);
  assert.equal(rows[1].result, "info");
  // The row's result is the leakage finding; the ID column is not listed as left out.
  assert.equal(rows[2].result, "ok");
  assert.match(rows[2].text, /churned_at/);
  assert.doesNotMatch(rows[2].text, /\bid\b/);
  assert.equal(dataCheckRows(computed, [{ check: "duplicate_rows", status: "not_evaluated", message: "x" }])[0].result, "unknown");
  assert.match(dataCheckRows(computed, [], undefined, { findingsLoading: true })[0].text, /Loading/);
  assert.match(dataCheckRows(computed, null, undefined, { findingsError: true })[0].text, /could not be loaded/);
});
