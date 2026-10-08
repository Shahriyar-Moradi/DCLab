import assert from "node:assert/strict";
import test from "node:test";
import { columnUse, leakageExcluded, compareRoles, dataCheckRows, fileSummary, percent, pickDatasetId, preparedIds, roleLabel, typeLabel, usedBy, usedByText } from "./studio-data.ts";

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

test("fileSummary shows only what was returned", () => {
  const dataset = { row_count: 48210, column_count: 31 };
  assert.deepEqual(fileSummary(dataset, null).map((i) => i.key), ["rows", "cols"]);
  const noSplit = { statistics_status: "no_split_plan", split_plan: null, experiment: null, columns: [] };
  assert.deepEqual(fileSummary(dataset, noSplit).map((i) => i.key), ["rows", "cols"]);
  const computed = {
    statistics_status: "computed", split_plan: { training_row_count: 39306, target_column: "churned" }, experiment: {},
    columns: [col({ name: "a" }), col({ name: "id", role_used: "identifier", rule_role: "identifier" }), col({ name: "churned", role_used: "target" })],
  };
  const items = fileSummary(dataset, computed);
  assert.deepEqual(items.map((i) => i.key), ["rows", "cols", "train", "target", "used"]);
  assert.equal(items.find((i) => i.key === "used")?.value, "1 used, 1 left out");
  assert.equal(items.find((i) => i.key === "target")?.value, "churned");
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
