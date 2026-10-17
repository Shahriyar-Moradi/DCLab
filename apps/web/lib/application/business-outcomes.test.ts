import assert from "node:assert/strict";
import test from "node:test";
import {
  WORDS, formatDay, listDownloadId, outcomesView, pickProject, projectChoices, type OutcomesInput, type OutcomesLike,
} from "./business-outcomes.ts";

// Business-view words (v7): never on this page, in page words or in the API's sentences.
const BANNED = /\b(release|champion|ref|refs|digest|holdout|hold-out|feature contract|trust check|cv|cross-validation|candidate|experiment|folds?|accuracy|fresh estimate|L[0-3])\b/i;
const P1 = "11111111-1111-4111-8111-111111111111";
const P2 = "22222222-2222-4222-8222-222222222222";
const L1 = "33333333-3333-4333-8333-333333333333";

const ready = (extra: Partial<OutcomesLike> = {}): OutcomesLike => ({
  project_name: "Churn",
  state: "ready",
  message: null,
  model_in_use: { display_name: "Churn model", saved_at: "2026-10-01T23:30:00Z", approved_at: "2026-10-02T08:00:00Z", approval_text: "Approved for scoring by a person." },
  headline: {
    status: "reported",
    sentences: ['Of every 10 customers with churned "yes" in the final test, the model flagged 8.', "Of every 10 customers it flagged, 7 really had it."],
    counts_text: "Exact counts: 40 of 50 customers were flagged.",
    basis: "From the final test of the model in use: one scoring of 200 rows it never saw while it was being built.",
  },
  how_built: { sentences: ["It learned from 800 of the 1,000 rows.", "It is a set of many small decision trees.", "Then 200 rows were set aside and used once."] },
  weekly_list: { id: L1, completed_at: "2026-10-07T09:00:00Z", rows: 30, has_reasons: true, text: "The latest list has 30 rows, each with a score and up to 3 reasons.", download_path: `/v1/predictions/${L1}/download` },
  weekly_list_note: null,
  limits: ["The model can be wrong about any single row: a score is an estimate, not a certainty."],
  boundary: "DCLab scores and explains; it does not contact customers or take business actions.",
  ...extra,
});
const input = (extra: Partial<OutcomesInput> = {}): OutcomesInput => ({
  projectsPending: false, projectsFailed: false, choices: [{ id: P1, name: "Churn" }], pending: false, failedStatus: null, data: ready(), ...extra,
});

test("the API's projects with a model in use are offered; the requested one only when it is offered", () => {
  const choices = projectChoices({ items: [
    { id: P1, name: "Churn\u0000\u202e  forecast" },
    { id: P2, name: "x".repeat(200) },
    { id: "33333333-3333-4333-8333-33333333333z", name: "Bad id" },
    { id: "__proto__", name: "Odd id" },
  ] });
  assert.deepEqual(choices.map((c) => c.id), [P1, P2]);
  assert.equal(choices[0].name, "Churn forecast");
  assert.ok(choices[1].name.length <= 80);
  assert.equal(pickProject(choices, P2), P2);
  for (const requested of [null, undefined, "", "__proto__", "constructor", "55555555-5555-4555-8555-555555555555", `${P2}/x`]) {
    assert.equal(pickProject(choices, requested), P1, String(requested));
  }
  assert.equal(pickProject([], P1), null);
  assert.deepEqual(projectChoices(undefined), []);
});

test("loading, failed, no project, not found, no model, no final result and ready are distinct", () => {
  assert.equal(outcomesView(input({ projectsPending: true })).kind, "loading");
  assert.deepEqual(outcomesView(input({ projectsFailed: true })), { kind: "error", message: WORDS.failed });
  assert.deepEqual(outcomesView(input({ choices: [] })), { kind: "no_projects", message: WORDS.noProjects });
  assert.deepEqual(outcomesView(input({ failedStatus: 404, data: undefined })), { kind: "error", message: WORDS.notFound });
  assert.deepEqual(outcomesView(input({ failedStatus: 500, data: undefined })), { kind: "error", message: WORDS.failed });
  assert.equal(outcomesView(input({ pending: true, data: undefined })).kind, "loading");
  const none = outcomesView(input({ data: { project_name: "Fresh", state: "no_model", message: "No model is in use for this project yet.", boundary: "B" } }));
  assert.deepEqual(none, { kind: "no_model", projectName: "Fresh", message: "No model is in use for this project yet.", boundary: "B" });
  const missing = outcomesView(input({ data: ready({ state: "no_result", message: "The model in use has no final test result.", headline: { status: "missing", sentences: [], basis: "x" }, how_built: null }) }));
  assert.equal(missing.kind, "ready");
  if (missing.kind !== "ready") return;
  assert.deepEqual(missing.outcome, []);
  assert.equal(missing.outcomeNotice, "The model in use has no final test result.");
  assert.equal(missing.counts, null);
  assert.equal(missing.basis, null);
  assert.deepEqual(missing.built, []);
  const withheld = outcomesView(input({ data: ready({ headline: { status: "withheld", basis: "Shown to people only.", counts_text: "secret", sentences: ["secret"] } }) }));
  assert.ok(withheld.kind === "ready" && withheld.outcome.length === 0 && withheld.counts === null && withheld.outcomeNotice === "Shown to people only.");
});

test("a ready view keeps the API's sentences as capped plain text, in page order", () => {
  const view = outcomesView(input({ data: ready({ project_name: "Churn\n<b>q3</b>", headline: { status: "reported", sentences: ["a".repeat(900)], basis: "b" } }) }));
  assert.equal(view.kind, "ready");
  if (view.kind !== "ready") return;
  assert.equal(view.projectName, "Churn <b>q3</b>");
  assert.ok(view.outcome[0].length <= 400);
  assert.deepEqual(view.dates, ["Saved on 1 Oct 2026", "Approved for scoring on 2 Oct 2026"]);
  assert.deepEqual(view.list, { id: L1, scoredOn: "7 Oct 2026", text: "The latest list has 30 rows, each with a score and up to 3 reasons." });
  assert.equal(view.listNote, null);
  const noList = outcomesView(input({ data: ready({ weekly_list: null, weekly_list_note: "No list has been scored with the model in use yet.", model_in_use: { display_name: "M", saved_at: "nope", approval_text: "a" } }) }));
  assert.ok(noList.kind === "ready" && noList.list === null && noList.listNote === "No list has been scored with the model in use yet.");
  assert.ok(noList.kind === "ready" && noList.dates.length === 0);
});

test("the download opens only the prediction's own DCLab download path", () => {
  const list = ready().weekly_list!;
  assert.equal(listDownloadId(list), L1);
  assert.equal(listDownloadId({ ...list, download_path: `/api/backend/v1/predictions/${L1}/download` }), L1);
  for (const path of [`/v1/predictions/${P1}/download`, `https://evil.example/v1/predictions/${L1}/download`, `/v1/predictions/${L1}/download?x=1`, "", "__proto__"]) {
    assert.equal(listDownloadId({ ...list, download_path: path }), null, path);
  }
  assert.equal(listDownloadId({ ...list, id: "__proto__" }), null);
  assert.equal(listDownloadId(null), null);
});

test("dates are days in UTC; unreadable dates are left out", () => {
  assert.equal(formatDay("2026-10-07T23:59:00Z"), "7 Oct 2026");
  assert.equal(formatDay("not a date"), null);
  assert.equal(formatDay(null), null);
});

test("no page word or sample API sentence uses a banned word", () => {
  const view = outcomesView(input());
  const texts: string[] = [...Object.values(WORDS)];
  if (view.kind === "ready") texts.push(...view.outcome, view.counts ?? "", view.basis ?? "", ...view.built, view.list?.text ?? "", ...view.limits, view.boundary, ...view.dates, view.approval ?? "");
  assert.ok(texts.length > 30);
  for (const text of texts) assert.doesNotMatch(text, BANNED, text);
});
