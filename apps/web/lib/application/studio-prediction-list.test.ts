import assert from "node:assert/strict";
import test from "node:test";
import {
  LIST_SOURCE, NOT_CAUSES, WEEK_MS, downloadError, listColumns, listError, listHeading, listNotes, listRows, pickList, predictionTiles, type RowsPageLike, type ScoredLike,
} from "./studio-prediction-list.ts";

const A = "11111111-1111-4111-8111-111111111111";
const B = "22222222-2222-4222-8222-222222222222";
const C = "33333333-3333-4333-8333-333333333333";
const BANNED = /\b(release|champion|ref|digest|manifest|package|slot|tick|revision|holdout|worker|artifact|cursor|idempotency)s?\b/i;
const scored = (id: string, extra: Partial<ScoredLike> = {}): ScoredLike => ({ id, status: "completed", rows_in: 51296, rows_out: 51296, completed_at: "2026-10-05T02:00:00Z", decision_threshold: 0.31, ...extra });

test("the list shown: the one asked for when finished here, else the newest finished; none when nothing finished", () => {
  const list = [scored(A, { completed_at: "2026-10-01T00:00:00Z" }), scored(B, { completed_at: "2026-10-08T00:00:00Z" }), scored(C, { status: "running", completed_at: null })];
  assert.equal(pickList(list, null)?.id, B);
  assert.equal(pickList(list, A)?.id, A);
  assert.equal(pickList(list, C)?.id, B, "a scoring still running is never shown as a list");
  assert.equal(pickList(list, "__proto__")?.id, B);
  assert.equal(pickList([scored(C, { status: "failed" })], null), null);
});

test("tiles: flagged count and share from the read's own count, rows scored, when, UTC", () => {
  const tiles = predictionTiles(scored(A, { flagged: { count: 6412, rows: 51296, share: 0.125, decision_threshold: 0.31 } }));
  assert.equal(tiles.flagged.value, "6,412 (12.5%)");
  assert.equal(tiles.flagged.hint, "score at or above the threshold 0.31");
  assert.equal(tiles.scored.value, "51,296");
  assert.equal(tiles.last.value, "5 Oct 2026");
  assert.equal(tiles.last.label, "scored");
  assert.equal(tiles.last.hint, "at 02:00 UTC, when scored");
  const noShare = predictionTiles(scored(A, { flagged: { count: 3, rows: 12, share: null } }));
  assert.equal(noShare.flagged.value, "3 (25.0%)");
  const older = predictionTiles(scored(A, { flagged: null }));
  assert.equal(older.flagged.value, "—");
  assert.match(older.flagged.hint, /not recorded for this scoring/);
  const multi = predictionTiles(scored(A, { flagged: null, decision_threshold: null, rows_out: null, completed_at: null }));
  assert.match(multi.flagged.hint, /does not apply/);
  assert.equal(multi.scored.value, "—");
  assert.equal(multi.last.value, "—");
  for (const t of [tiles, older, multi]) assert.doesNotMatch(JSON.stringify(t), BANNED);
});

const page = (extra: Partial<RowsPageLike> = {}): RowsPageLike => ({
  task_type: "binary", order: "score", entity_column: "customer_id", decision_threshold: 0.31, rows_total: 51296, rows_reachable: 10000,
  items: [], reasons: { available: true, method: "tree_shap", rows_explained: 51296 }, ...extra,
});

test("rows: the customer's keys, classes and reasons as capped plain text, at most three reasons", () => {
  const rows = listRows([
    { row_number: 1204, entity: "C-48112‮<script>", probability: 0.912, flagged: true, reasons: ["no login for 61 days", "", "fee: above training median, raised the score", "fourth", "fifth"] },
    { row_number: 7, entity: null, probability: null, flagged: false, reasons: ["x".repeat(500)] },
    { row_number: 8, label: "gold", probability: 0.5 },
    { row_number: 9, prediction: 12.3456 },
  ]);
  assert.equal(rows[0].row, "1,204");
  assert.equal(rows[0].entity, "C-48112 <script>", "rendered as text by React, never markup; bidi controls removed");
  assert.equal(rows[0].score, "0.91");
  assert.equal(rows[0].flagged, "yes");
  assert.deepEqual(rows[0].reasons, ["no login for 61 days", "fee: above training median, raised the score", "fourth"]);
  assert.equal(rows[1].entity, "—");
  assert.equal(rows[1].score, "—");
  assert.equal(rows[1].flagged, "no");
  assert.equal(rows[1].reasons[0].length, 200);
  assert.equal(rows[2].answer, "gold");
  assert.equal(rows[2].flagged, "—");
  assert.equal(rows[3].answer, "12.3");
});

test("columns follow the kind of model and what the rows carry", () => {
  const items = [{ row_number: 1, entity: "C-1", reasons: ["a"] }];
  assert.deepEqual(listColumns(page(), items), { entity: "customer_id", score: true, flagged: true, label: false, prediction: false, reasons: true });
  assert.deepEqual(listColumns(page({ task_type: "multiclass", entity_column: null }), [{ row_number: 1 }]), { entity: null, score: true, flagged: false, label: true, prediction: false, reasons: false });
  assert.equal(listColumns(page({ entity_column: null }), items).entity, "Key");
  const regression = listColumns(page({ task_type: "regression" }), [{ row_number: 1, reasons: ["", " "] }]);
  assert.equal(regression.score, false);
  assert.equal(regression.prediction, true);
  assert.equal(regression.reasons, false);
});

test("notes: counts, the reachable cap, the threshold, 'what the model used, not causes', the explained cap", () => {
  const notes = listNotes(page({ reasons: { available: true, method: "occlusion", rows_explained: 1000 } }), 50);
  assert.equal(notes[0], "Showing 50 of 51,296 rows, highest score first.");
  assert.ok(notes.includes("Only the first 10,000 rows in this order can be listed here; download the file for every row."));
  assert.ok(notes.includes("Flagged means a score of 0.31 or more (the model's locked threshold)."));
  assert.ok(notes.includes(NOT_CAUSES));
  assert.match(NOT_CAUSES, /what the model used for a score, not causes/);
  assert.ok(notes.some((n) => /approximate/.test(n)));
  assert.ok(notes.includes("Reasons are filled for the first 1,000 rows of the file only."));
  const plain = listNotes(page({ order: "row", rows_total: 40, rows_reachable: 40, reasons: null, task_type: "regression" }), 40);
  assert.deepEqual(plain, ["Showing 40 of 40 rows, in file order.", "This scoring has no reasons in its file. The scores are not affected."]);
  const unknownMethod = listNotes(page({ reasons: { available: true, method: "__proto__", rows_explained: 51296 } }), 50);
  assert.ok(!unknownMethod.some((n) => /^Reasons come from/.test(n)));
  for (const n of [...notes, ...plain]) assert.doesNotMatch(n, BANNED);
});

test("rows refusals: fixed sentences by code then status, never the server message", () => {
  const err = (status: number, code = "", message = "SERVER WORDS digest") => ({ status, body: { error: { code, message } } });
  assert.match(listError(err(409, "prediction_rows_too_large")).text, /too large to list here/);
  assert.equal(listError(err(409, "prediction_rows_too_large")).retry, false);
  assert.match(listError(err(429, "rows_busy")).text, /Too many lists are being read right now/);
  assert.equal(listError(err(429, "rows_busy")).retry, true);
  assert.match(listError(err(429)).text, /Too many lists/);
  assert.match(listError(err(409, "prediction_not_ready")).text, /once this scoring has finished/);
  assert.match(listError(err(404, "not_found")).text, /not in the active workspace/);
  assert.match(listError(err(403, "capability_denied")).text, /cannot read this list/);
  assert.match(listError(err(422, "validation_failed")).text, /Start again from the top/);
  assert.deepEqual(listError(err(400, "invalid_cursor")), { text: "The list moved on. Start again from the top.", retry: false, restart: true });
  assert.deepEqual(listError(err(409, "prediction_output_changed")), { text: "The saved predictions file changed since it was made; it cannot be listed.", retry: false });
  assert.deepEqual(listError(err(409, "prediction_rows_unavailable")), { text: "This predictions file cannot be listed here; download it.", retry: false });
  assert.match(listError(err(500, "__proto__")).text, /could not be loaded/);
  assert.deepEqual(listError(err(409, "constructor")), { text: "This list cannot be shown here. Download the file to see every row.", retry: false }, "any other 409 is not retryable");
  assert.match(listError(new TypeError("Failed to fetch")).text, /could not be loaded/);
  for (const [s, c] of [[409, "prediction_rows_too_large"], [409, "prediction_output_changed"], [409, "prediction_rows_unavailable"], [409, ""], [400, "invalid_cursor"], [429, "rows_busy"], [404, ""], [403, ""], [422, ""], [500, ""]] as const) {
    const text = listError(err(s, c)).text;
    assert.doesNotMatch(text, /SERVER WORDS/);
    assert.doesNotMatch(text, BANNED);
  }
});

test("heading: this week's list only within 7 days; source sentence; download refusals by code", () => {
  const now = Date.parse("2026-10-16T12:00:00Z");
  assert.equal(listHeading("2026-10-15T12:00:00Z", now), "This week's list");
  assert.equal(listHeading(new Date(now - WEEK_MS - 1000).toISOString(), now), "Newest list");
  assert.equal(listHeading(null, now), "Newest list");
  assert.equal(listHeading("not a date", now), "Newest list");
  assert.match(LIST_SOURCE, /other scorings are not listed yet/);
  const err = (status: number, code = "") => ({ status, body: { error: { code, message: "SERVER WORDS" } } });
  assert.match(downloadError(err(409, "prediction_output_changed")), /changed since it was made/);
  assert.match(downloadError(err(409, "prediction_not_ready")), /once this scoring has finished/);
  assert.match(downloadError(err(404)), /not in the active workspace/);
  assert.match(downloadError(err(500, "__proto__")), /could not be downloaded/);
  assert.match(downloadError(new Error("The download link from the API is not a DCLab path")), /could not be downloaded/);
  for (const t of [LIST_SOURCE, downloadError(err(403)), downloadError(err(429))]) { assert.doesNotMatch(t, BANNED); assert.doesNotMatch(t, /SERVER WORDS/); }
});
