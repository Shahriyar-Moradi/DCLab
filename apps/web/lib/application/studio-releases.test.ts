import assert from "node:assert/strict";
import test from "node:test";
import {
  APPROVAL_NOT_CURRENT, NO_LONGER_IN_USE, REASON_MAX, approvalLine, approvalSummary, canPutInUse, confirmWords, isConflict, mapApprovalError, modelAction,
  reasonToSend, releaseRows, rowAction, scoredWithText, scoringApproval, targetStillOffered, unapprovedModels, type ReleaseLike,
} from "./studio-releases.ts";

const M1 = "11111111-1111-4111-8111-111111111111";
const M2 = "22222222-2222-4222-8222-222222222222";
const M3 = "33333333-3333-4333-8333-333333333333";
const ME = "99999999-9999-4999-8999-999999999999";
const when = (iso: string) => `on-${iso.slice(0, 10)}`;
const rel = (id: string, mv: string, version: string, at: string, extra: Partial<ReleaseLike> = {}): ReleaseLike => ({
  id, model_version_id: mv, model_version: version, approved_at: at, is_current: false, saved_format: "package_v1",
  decision_record_id: "dddddddd-dddd-4ddd-8ddd-dddddddddddd", approved_by_user_id: "88888888-8888-4888-8888-888888888888", rationale: "because", ...extra,
});
const R1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1";
const R2 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2";
const R3 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3";
const BANNED = /release|champion|\bref\b|digest|manifest|package|idempotency|holdout/i;

test("approvals: newest first, you or a person, one go-back per older model, never on the model in use", () => {
  const items = [
    rel(R1, M1, "1", "2026-10-01T10:00:00Z", { approved_by_user_id: ME }),
    rel(R3, M2, "v2", "2026-10-03T10:00:00Z", { is_current: true, rationale: "  better‮recall \n now " }),
    rel(R2, M1, "1", "2026-10-02T10:00:00Z", { approved_by_user_id: null, rationale: null, saved_format: "legacy_quarantined" }),
    rel("not-a-uuid", M1, "1", "2026-10-04T10:00:00Z"),
  ];
  const rows = releaseRows(items, { myUserId: ME, inUseModelId: M2, when });
  assert.deepEqual(rows.map((r) => r.id), [R3, R2, R1]);
  assert.equal(rows[0].model, "Model v2");
  assert.equal(rows[0].current, true);
  assert.equal(rows[0].goBack, false);
  assert.equal(rows[0].reason, "better recall now");
  assert.equal(rows[0].line, "Approved for scoring by a person on on-2026-10-03.");
  assert.equal(rows[1].goBack, true, "the newest approval of an older model offers going back");
  assert.equal(rows[1].reason, null);
  assert.equal(rows[1].oldFormat, true);
  assert.equal(rows[1].line, "Earlier approval by a person on on-2026-10-02. Not the model in use now.");
  assert.equal(rows[2].goBack, false, "only once per model");
  assert.equal(rows[2].by, "you");
  // the model in use from the refs is never offered as "go back", even without a current approval
  assert.ok(releaseRows([rel(R1, M1, "1", "2026-10-01T10:00:00Z")], { myUserId: null, inUseModelId: M1, when }).every((r) => !r.goBack));
  // a long reason is capped
  assert.ok((releaseRows([rel(R1, M1, "1", "2026-10-01T10:00:00Z", { rationale: "x".repeat(5000) })], { myUserId: null, inUseModelId: null, when })[0].reason ?? "").length <= 400);
});

test("re-approval R1(M1) -> R2(M2) -> R3(M1): the older M1 approval is not the current one, but M1 is in use", () => {
  const rows = releaseRows([
    rel(R1, M1, "1", "2026-10-01T10:00:00Z"), rel(R2, M2, "2", "2026-10-02T10:00:00Z"), rel(R3, M1, "1", "2026-10-03T10:00:00Z", { is_current: true }),
  ], { myUserId: null, inUseModelId: M1, when });
  assert.deepEqual(rows.map((r) => [r.id, r.goBack]), [[R3, false], [R2, true], [R1, false]]);
  assert.equal(rows[2].line, "Earlier approval by a person on on-2026-10-01. This model is in use under a newer approval.");
  // a scoring that ran with R1 while M1 is (again) in use: only the approval changed
  const r1 = { id: R1, model_version_id: M1, approved_at: "2026-10-01T10:00:00Z", is_current: false };
  assert.equal(scoringApproval("completed", r1, when, true).stale, APPROVAL_NOT_CURRENT);
  assert.equal(scoringApproval("completed", r1, when, null).stale, APPROVAL_NOT_CURRENT);
  assert.equal(scoringApproval("completed", r1, when, false).stale, NO_LONGER_IN_USE);
});

test("summary: loading, failed, approved, in use without approval and none are distinct", () => {
  const rows = releaseRows([rel(R3, M2, "2", "2026-10-03T10:00:00Z", { is_current: true, approved_by_user_id: ME })], { myUserId: ME, inUseModelId: M2, when });
  assert.equal(approvalSummary({ pending: true, failed: false, currentReleaseId: null, rows: [], inUse: null }).kind, "loading");
  assert.equal(approvalSummary({ pending: false, failed: true, currentReleaseId: null, rows, inUse: null }).kind, "error");
  const approved = approvalSummary({ pending: false, failed: false, currentReleaseId: R3, rows, inUse: { id: M2, name: "Model v2" } });
  assert.equal(approved.kind === "approved" && approved.text, "Model v2 is the model in use. It was approved for scoring by you on on-2026-10-03.");
  const first = approvalSummary({ pending: false, failed: false, currentReleaseId: null, rows: [], inUse: { id: M1, name: "Model v1" } });
  assert.equal(first.kind, "unrecorded");
  assert.match(first.kind === "unrecorded" ? first.text : "", /no approval is recorded/);
  assert.doesNotMatch(first.kind === "unrecorded" ? first.text : "", /approved by/);
  assert.equal(approvalSummary({ pending: false, failed: false, currentReleaseId: null, rows: [], inUse: null }).kind, "none");
});

test("not approved yet, buttons by role, and a confirm step closes when its model is no longer offered", () => {
  const models = [{ id: M1, version: "1", champion: false }, { id: M2, version: "2", champion: false }, { id: M3, version: "3", champion: true }];
  const unapproved = unapprovedModels(models, [rel(R1, M2, "2", "2026-10-01T10:00:00Z")]);
  assert.deepEqual(unapproved.map((m) => m.id), [M3, M1]);
  // a viewer (no write access) sees no button; a writer sees none while a confirm step is open
  assert.equal(rowAction({ goBack: true }, { canWrite: false, confirming: false }), null);
  assert.equal(modelAction({ canWrite: false, confirming: false }), null);
  assert.equal(rowAction({ goBack: true }, { canWrite: true, confirming: false }), "go_back");
  assert.equal(rowAction({ goBack: false }, { canWrite: true, confirming: false }), null);
  assert.equal(modelAction({ canWrite: true, confirming: true }), null);
  assert.equal(modelAction({ canWrite: true, confirming: false }), "approve");
  assert.equal(canPutInUse(false, false), false);
  assert.equal(canPutInUse(true, true), false);
  assert.equal(canPutInUse(true, false), true);
  assert.equal(targetStillOffered(M1, [], unapproved), true);
  assert.equal(targetStillOffered(M2, [], unapproved), false, "approved by someone else meanwhile");
});

test("confirm step says what changes, what it replaces and what does not change", () => {
  const go = confirmWords("go_back", "Model v1", "Model v2");
  assert.equal(go.title, "Go back to Model v1?");
  const text = go.lines.join(" ");
  assert.match(text, /its features move with it/);
  assert.match(text, /Model v2 stops being the model in use/);
  assert.match(text, /Predictions already made are not changed/);
  assert.match(text, /Nothing is retrained/);
  assert.match(text, /saved model file exists in a format DCLab can score with/);
  assert.match(text, /same test design as the model in use/);
  assert.match(text, /weekly schedule is planned and not built/);
  assert.doesNotMatch(text, BANNED);
  assert.doesNotMatch(text, /final test/i);
  assert.match(confirmWords("approve", "Model v1", "Model v1").lines.join(" "), /Model v1 stays the model in use; this records its approval/);
  assert.match(confirmWords("approve", "Model v1", null).lines.join(" "), /No model is in use now/);
  assert.equal(confirmWords("approve", "Model v2", null).button, "Yes, approve Model v2");
  assert.equal(reasonToSend("approve", "Model v2", "  "), "Approved Model v2 for scoring on the Model screen (no reason given).");
  assert.equal(reasonToSend("go_back", "Model v1", ""), "Went back to Model v1 on the Model screen (no reason given).");
  assert.equal(reasonToSend("approve", "Model v2", ` ${"y".repeat(REASON_MAX + 10)} `).length, REASON_MAX);
});

// The API's real envelopes: a generic code with the precise reason in details (V1APIError + DecisionRecordError.public_detail).
const env = (status: number, code: string, reason?: string) => ({
  status, body: { error: { code, message: "server words: secret-ish internal text", retryable: false, details: reason === undefined ? {} : { reason } } },
});
const PROBES = [
  env(422, "invalid_decision_record", "legacy_model_quarantined"),
  env(422, "invalid_decision_record", "model_package_missing"),
  env(422, "invalid_decision_record", "champion_evidence_unavailable"),
  env(422, "invalid_decision_record", "__proto__"),
  env(422, "invalid_decision_record", "constructor"),
  env(409, "ref_version_conflict", "release_created_concurrently"),
  env(409, "ref_version_conflict", "stale_ref_version"),
  env(409, "champion_split_plan_mismatch", "champion_split_plan_mismatch"),
  env(409, "idempotency_key_conflict", "idempotency_key_conflict"),
  env(404, "ref_target_not_found", "ref_target_not_found"),
  env(403, "capability_denied"),
  env(403, "decision_actor_not_permitted", "human_only"),
  env(412, "precondition_failed"),
  env(428, "precondition_required"),
  env(422, "validation_failed"),
  env(429, "rate_limited"),
  env(500, "internal_error"),
  new Error("Failed to fetch"),
  null,
];

test("approval refusals map to fixed plain sentences, reason first; the server message is never shown", () => {
  const t = (e: unknown) => mapApprovalError(e).title;
  assert.equal(t(PROBES[0]), "This model was saved in the old format");
  assert.match(mapApprovalError(PROBES[0]).detail, /until it is trained again/);
  assert.equal(t(PROBES[1]), "This model has no saved model file");
  assert.equal(t(PROBES[2]), "This model cannot be approved yet");
  assert.equal(t(PROBES[3]), "This model cannot be approved yet", "__proto__ is not a known reason");
  assert.equal(t(PROBES[5]), "Someone else just changed the model in use");
  assert.equal(t(PROBES[6]), "Someone else just changed the model in use");
  assert.equal(t(PROBES[7]), "Not comparable with the model in use");
  assert.equal(t(PROBES[8]), "Your approval may already be saved");
  assert.equal(t(PROBES[9]), "This model is not in this project");
  assert.equal(t(PROBES[10]), "You do not have permission to approve a model here");
  assert.equal(t(PROBES[11]), "You do not have permission to approve a model here");
  assert.equal(t(PROBES[12]), "Someone else just changed the model in use");
  assert.equal(t(PROBES[17]), "The approval was not saved");
  assert.equal(isConflict(PROBES[5]), true);
  assert.equal(isConflict(PROBES[17]), false);
  for (const probe of PROBES) {
    const e = mapApprovalError(probe);
    assert.equal(typeof e.title, "string");
    assert.equal(typeof e.detail, "string");
    assert.doesNotMatch(`${e.title} ${e.detail}`, /server words|secret-ish/);
    assert.doesNotMatch(`${e.title} ${e.detail}`, BANNED);
  }
});

test("scoring approval follows the status: scored only when completed, never an approval for a failed one", () => {
  const r = { id: R1, model_version_id: M1, approved_at: "2026-10-01T10:00:00Z", is_current: true };
  assert.equal(scoredWithText(scoringApproval("completed", r, when, true), "Model v1"), "Scored with: Model v1 · approved on-2026-10-01");
  assert.equal(scoredWithText(scoringApproval("queued", r, when, true), "Model v1"), "Scoring with: Model v1 · approved on-2026-10-01");
  assert.equal(scoredWithText(scoringApproval("running", r, when, true), "Model v1"), "Scoring with: Model v1 · approved on-2026-10-01");
  for (const status of ["failed", "cancelled", "__proto__"]) {
    const s = scoringApproval(status, r, when, true);
    assert.equal(scoredWithText(s, "Model v1"), "Was to score with: Model v1");
    assert.equal(s.stale, null);
  }
  const none = scoringApproval("completed", null, when, true);
  assert.match(none.approval ?? "", /no approval recorded/);
  assert.doesNotMatch(scoredWithText(none, "Model v1"), /· approved /);
  assert.deepEqual(scoringApproval("completed", undefined, when, true), none);
  assert.equal(scoringApproval("completed", r, when, true).stale, null);
});

test("model page line: in use with or without an approval; a model not in use now never reads as unapproved for ever", () => {
  const r = { id: R1, model_version_id: M1, approved_at: "2026-10-01T10:00:00Z", is_current: true };
  assert.equal(approvalLine(r, true, when), "Yes, approved on on-2026-10-01");
  assert.equal(approvalLine(null, true, when), "In use, but no approval is recorded");
  assert.match(approvalLine(null, false, when), /^Not the model in use now/);
  assert.doesNotMatch(approvalLine(null, false, when), /^No\b/);
});
