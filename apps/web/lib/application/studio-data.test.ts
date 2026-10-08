import assert from "node:assert/strict";
import test from "node:test";
import { compareRoles, percent, pickDatasetId, preparedIds, roleLabel, usedBy, usedByText } from "./studio-data.ts";

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
  assert.equal(usedByText(usedBy(edges, D1)), "1 split plan · 1 run · 1 model");
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
