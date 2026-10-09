import assert from "node:assert/strict";
import test from "node:test";

import { dataVersionName, designLabels, modelName, runName, runOrdinals } from "./studio-names.ts";

test("runs are numbered by start time, stably", () => {
  const ord = runOrdinals([
    { id: "b", created_at: "2026-06-02T00:00:00Z" },
    { id: "a", created_at: "2026-06-01T00:00:00Z" },
    { id: "c", created_at: "2026-06-02T00:00:00Z" },
  ]);
  assert.deepEqual([ord.get("a"), ord.get("b"), ord.get("c")], [1, 2, 3]);
  assert.equal(runName(3, "LightGBM"), "Run 3 · LightGBM");
  assert.equal(runName(2), "Run 2");
  assert.equal(runName(undefined, "x"), "Run · x");
});

test("model versions read as Model vN", () => {
  assert.equal(modelName("2"), "Model v2");
  assert.equal(modelName("v3"), "Model v3");
  assert.equal(modelName(""), "Model");
  assert.equal(modelName(null), "Model");
});

test("designs share a label when they share an id", () => {
  const labels = designLabels(["s1", null, "s2", "s1"]);
  assert.equal(labels.get("s1"), "Test design 1");
  assert.equal(labels.get("s2"), "Test design 2");
  assert.equal(labels.size, 2);
});

test("data versions are a file name and a date, never a code", () => {
  assert.match(dataVersionName("churn.csv", "2026-06-12T10:00:00Z"), /^churn\.csv · 12 Jun 2026$/);
  assert.equal(dataVersionName(null, null), "Data file");
  assert.equal(dataVersionName("a.csv", "bad"), "a.csv");
});

test("evidence scope never says holdout", async () => {
  const { evidenceScopeLabel } = await import("./studio-names.ts");
  assert.equal(evidenceScopeLabel("final_holdout"), "final test set (used once per run)");
  assert.equal(evidenceScopeLabel("cv_aggregate"), "cv aggregate");
});

test("breadcrumb names come from own keys only", async () => {
  const { offSidebarCrumb } = await import("./studio-names.ts");
  assert.equal(offSidebarCrumb("graph"), "Lineage");
  for (const hostile of ["__proto__", "constructor", "toString", "hasOwnProperty", undefined, ""]) assert.equal(offSidebarCrumb(hostile), undefined, String(hostile));
});

test("with a partial run list a run is named by its short id, not a page-dependent number", async () => {
  const { runName } = await import("./studio-names.ts");
  assert.equal(runName(3, "x", "ab12cd34"), "Run ab12cd34 · x");
  assert.equal(runName(3, null, "ab12cd34"), "Run ab12cd34");
});
