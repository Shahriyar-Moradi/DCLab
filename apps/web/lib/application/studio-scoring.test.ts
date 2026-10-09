import assert from "node:assert/strict";
import test from "node:test";
import {
  contractSentences, contractView, downloadWords, failureError, historyRows, mapScoringError, modelChoiceLabel, parseSessionScorings, pickModel, pollDelay, safeDownloadId, scoringStatusWords, sessionStoreKey, type ReadState,
} from "./studio-scoring.ts";

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

const M1 = "22222222-2222-4222-8222-222222222222";
const M2 = "33333333-3333-4333-8333-333333333333";

test("the model to score with: asked-for if it is this project's, else the model in use, else the newest, else none", () => {
  const models = [{ id: M1, champion: false }, { id: M2, champion: true }];
  assert.equal(pickModel(models, M1), M1);
  assert.equal(pickModel(models, null), M2);
  assert.equal(pickModel(models, "44444444-4444-4444-8444-444444444444"), M2);
  assert.equal(pickModel([{ id: M1, champion: false }], undefined), M1);
  assert.equal(pickModel([], M1), null);
  assert.equal(modelChoiceLabel({ version: "2", champion: true }, "Run 3"), "Model v2 · Run 3 (in use)");
  assert.equal(modelChoiceLabel({ version: "v1", champion: false }, null), "Model v1");
});

test("words for status and for the downloaded file claim only what the API says", () => {
  assert.equal(scoringStatusWords("completed"), "done");
  assert.equal(scoringStatusWords("queued"), "waiting");
  assert.equal(scoringStatusWords("__proto__"), "proto");
  assert.equal(scoringStatusWords("toString"), "toString");
  assert.match(downloadWords(0.5), /one row for each row of your file.*row number.*entity column when your file has one.*0\/1 label.*locked threshold/);
  assert.doesNotMatch(downloadWords(0.5), /reason|why|top/i);
  assert.match(downloadWords(null), /predicted number/);
  assert.doesNotMatch(downloadWords(null), /threshold/);
});

test("history: newest first, you, the model by name, a file name is never reworded", () => {
  const entries = [
    { id: ID, fileName: "holdout_next-month.csv", modelVersionId: M1 },
    { id: "55555555-5555-4555-8555-555555555555", fileName: "__proto__.csv", modelVersionId: M2 },
    { id: "66666666-6666-4666-8666-666666666666", fileName: "late.csv", modelVersionId: M2 },
  ];
  const reads = new Map<string, ReadState>([
    [ID, { data: { id: ID, model_version_id: M1, status: "completed", created_at: "2026-10-01T10:00:00Z", rows_in: 40, rows_out: 40 } }],
    [entries[1].id, { data: { id: entries[1].id, model_version_id: M2, status: "failed", created_at: "2026-10-02T10:00:00Z", rows_in: null, rows_out: null } }],
    // a read that belongs to another model than the entry says is not trusted
    [entries[2].id, { data: { id: entries[2].id, model_version_id: M1, status: "completed", created_at: "2026-10-03T10:00:00Z", rows_in: 1, rows_out: 1 } }],
  ]);
  const rows = historyRows(entries, reads, (mv) => (mv === M1 ? "Model v1 · Run 1" : null), (mv) => `/m/${mv}`);
  assert.deepEqual(rows.map((r) => r.file), ["__proto__.csv", "holdout_next-month.csv", "late.csv"]);
  assert.equal(rows[1].rows, "40 of 40");
  assert.equal(rows[1].model, "Model v1 · Run 1");
  assert.equal(rows[0].model, "A model of this project");
  assert.equal(rows[0].status, "failed");
  assert.equal(rows[0].rows, "—");
  assert.equal(rows[2].status, "not this model's");
  assert.equal(rows[2].rows, "—");
  assert.ok(rows.every((r) => r.by === "You, in this browser tab"));
  // loading, failed read and no data are three different texts
  const three = historyRows(entries, new Map<string, ReadState>([[ID, undefined], [entries[1].id, { failed: true }]]), () => null, () => null);
  assert.deepEqual(three.map((r) => r.status), ["loading", "could not be read", "loading"]);
  assert.equal(three[1].statusKey, "failed");
});

test("the session list is kept per workspace and per person, never shared on one tab", () => {
  const a = sessionStoreKey({ userId: "user-a", workspaceId: "ws-1" }, M1);
  assert.notEqual(a, sessionStoreKey({ userId: "user-b", workspaceId: "ws-1" }, M1));
  assert.notEqual(a, sessionStoreKey({ userId: "user-a", workspaceId: "ws-2" }, M1));
  assert.notEqual(a, sessionStoreKey({ userId: "user-a", workspaceId: "ws-1" }, M2));
  assert.equal(a, `dclab.scorings.ws-1.user-a.${M1}`);
});
