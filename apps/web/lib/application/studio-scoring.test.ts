import assert from "node:assert/strict";
import test from "node:test";
import { contractSentences, contractView, failureError, mapScoringError, parseSessionScorings, pollDelay, safeDownloadId } from "./studio-scoring.ts";

const ID = "11111111-1111-4111-8111-111111111111";

test("a missing column is listed in plain words with what to do", () => {
  const view = contractView({ status: "failed", required_columns: ["a", "b", "c"], required_count: 3, missing_columns: ["b"], missing_count: 1, ignored_columns: ["x"], ignored_count: 1, target_column: "y", target_column_ignored: false });
  const out = contractSentences(view!);
  assert.equal(out.tone, "crit");
  assert.match(out.lines[0], /missing 1 column the model needs: b\./);
  assert.match(out.lines.join(" "), /1 other column is not used by the model and is ignored: x\./);
});

test("a passing check names ignored and target columns", () => {
  const out = contractSentences(contractView({ status: "passed", required_columns: ["a", "b"], required_count: 2, missing_columns: [], missing_count: 0, ignored_columns: ["id", "z"], ignored_count: 5, target_column: "churn", target_column_ignored: true })!);
  assert.equal(out.tone, "ok");
  assert.match(out.lines[0], /all 2 columns/);
  assert.match(out.lines.join(" "), /churn is the answer the model predicts, so it is ignored/);
  assert.match(out.lines.join(" "), /5 other columns are not used.*id, z and 3 more/);
  assert.equal(contractView(null), null);
});

test("training-file refusal and unknown codes read plainly", () => {
  const refused = mapScoringError({ status: 409, body: { error: { code: "TRAINING_DATASET_NOT_SCOREABLE", message: "x" } } });
  assert.match(refused.title, /file this model learned from/);
  assert.equal(mapScoringError({ status: 403 }).title, "You cannot score files here");
  assert.equal(mapScoringError({ status: 422, body: { error: { code: "upload_rejected" } } }).title, "That file could not be read as a table");
  assert.equal(failureError("feature_contract_failed", "the file is missing required columns: b").detail, "the file is missing required columns: b.");
  assert.equal(failureError(null, null).title, "Scoring did not finish");
});

test("poll delay backs off to five seconds", () => {
  assert.deepEqual([0, 1, 4, 20].map(pollDelay), [1000, 1500, 3000, 5000]);
});

test("only this prediction's own download path is used", () => {
  assert.equal(safeDownloadId(`/v1/predictions/${ID}/download`, ID), ID);
  assert.equal(safeDownloadId(`/api/backend/v1/predictions/${ID}/download`, ID), ID);
  assert.equal(safeDownloadId(`https://evil.example/v1/predictions/${ID}/download`, ID), null);
  assert.equal(safeDownloadId(`/v1/predictions/${ID}/download?token=1`, ID), null);
  assert.equal(safeDownloadId(`/v1/predictions/22222222-2222-4222-8222-222222222222/download`, ID), null);
  assert.equal(safeDownloadId(null, ID), null);
});

test("session history keeps only valid ids", () => {
  assert.deepEqual(parseSessionScorings(JSON.stringify([{ id: ID, fileName: "a.csv" }, { id: "bad", fileName: "b.csv" }, 3])), [{ id: ID, fileName: "a.csv" }]);
  assert.deepEqual(parseSessionScorings("not json"), []);
  assert.deepEqual(parseSessionScorings(null), []);
});

test("a warning status and empty columns are never an empty banner; scoring_failed keeps the API message", () => {
  const warn = contractSentences(contractView({ status: "warning", required_columns: ["a"], required_count: 1, missing_columns: [], missing_count: 0, parse_rates: { a: 0.4, b: 1 } })!);
  assert.equal(warn.tone, "warn");
  assert.match(warn.lines.join(" "), /could not be read and were treated as missing in: a\./);
  assert.match(contractSentences(contractView({ status: "failed", empty_columns: ["z"] })!).lines.join(" "), /empty in the file: z/);
  assert.equal(failureError("scoring_failed", "the input file could not be read as a table").detail, "the input file could not be read as a table.");
});
