import assert from "node:assert/strict";
import test from "node:test";
import {
  APPROVALS_SHORTCUT, LOG_EVENT_KINDS, approvalsHref, correctionFor, eventRow, evidenceHref, evidenceName, mergeLog, newerFileText, stateWords, subjectName, whatWords, whoWords,
  type ActivityEventLike, type EventRow,
} from "./studio-history.ts";

const ID = "00000000-0000-4000-8000-000000000001";

test("every decision type the API can write has a plain sentence; unknown types keep their own words", () => {
  const types = ["winner_locked", "split_plan_created", "ref_initialized", "problem_spec_locked", "ref_moved", "champion_promoted", "experiment_accepted", "experiment_rejected",
    "proposal_accepted", "proposal_rejected", "decision_point_resolved", "proposal_reverted", "operating_point_chosen"];
  for (const type of types) {
    const text = whatWords(type);
    assert.ok(!text.startsWith("Recorded:"), type);
    assert.ok(!/_|operating point|engine|supersede|proposal|holdout/i.test(text), text);
  }
  assert.equal(whatWords("new_kind_of_thing"), "Recorded: new kind of thing");
  assert.equal(whatWords("__proto__"), "Recorded: proto");
  assert.equal(whatWords("constructor"), "Recorded: constructor");
  assert.equal(whatWords(""), "Recorded: an entry");
});

test("who: you, the rules, a connected tool, the assistant; an unknown kind is never a rule", () => {
  assert.equal(whoWords({ kind: "human", user_id: "u1" }, "u1"), "You");
  assert.equal(whoWords({ kind: "human", user_id: "u2" }, "u1"), "A person");
  assert.equal(whoWords({ kind: "human", user_id: null }, "u1"), "A person");
  assert.equal(whoWords({ kind: "rule" }, "u1"), "The rules");
  assert.equal(whoWords({ kind: "agent", service_token_id: ID }, "u1"), "A connected tool (access token)");
  assert.equal(whoWords({ kind: "agent", agent_run_id: ID }, "u1"), "The assistant");
  assert.equal(whoWords({ kind: "agent" }, "u1"), "An agent");
  assert.equal(whoWords({ kind: "robot" }, "u1"), "Someone else");
  assert.equal(whoWords({ kind: "human", user_id: "u1" }, null), "A person");
});

test("correction is offered only where the API allows it", () => {
  assert.deepEqual(correctionFor("experiment_accepted", "proposed"), { kind: "answer", label: "Answer" });
  assert.deepEqual(correctionFor("experiment_accepted", "accepted"), { kind: "correct", label: "Correct" });
  assert.equal(correctionFor("experiment_accepted", "superseded").kind, "none");
  assert.equal(correctionFor("experiment_rejected", "rejected").kind, "none");
  for (const type of ["winner_locked", "split_plan_created", "problem_spec_locked", "ref_moved", "ref_initialized", "operating_point_chosen", "proposal_accepted"]) {
    assert.equal(correctionFor(type, "accepted").kind, "note", type);
  }
  assert.match((correctionFor("operating_point_chosen", "accepted") as { text: string }).text, /Threshold tab/);
  // fail closed: unknown types and AI/suggestion outcomes never get a Correct button
  for (const type of ["__proto__", "constructor", "something_new", "proposal_rejected"]) assert.equal(correctionFor(type, "accepted").kind, "none", type);
  for (const type of ["proposal_accepted", "proposal_reverted", "decision_point_resolved"]) {
    assert.deepEqual(correctionFor(type, "accepted"), { kind: "note", text: "Undo it from the suggestion in the Inbox (Revert)." }, type);
  }
  assert.equal((correctionFor("problem_spec_locked", "accepted") as { text: string }).text, "Change the goal by locking a new version of it.");
});

test("subjects and evidence use lineage names, then short run references, then the kind in words", () => {
  const names = new Map([[`experiment:${ID}`, "Run 2"], [`model_version:${ID}`, "Model v1"]]);
  assert.equal(subjectName({ kind: "experiment", id: ID }, names), "Run 2");
  assert.equal(subjectName({ kind: "experiment", id: "00000000-0000-4000-8000-0000000000aa" }, names), "Run 00000000");
  assert.equal(subjectName({ kind: "split_plan", id: ID }, names), "the test design");
  assert.equal(subjectName({ kind: "__proto__", id: ID }, names), "proto");
  assert.equal(evidenceName({ kind: "decision_record", id: ID }, names), "an earlier History entry");
  assert.equal(evidenceName({ kind: "model_version", id: ID }, names), "Model v1");
  assert.equal(evidenceName({ kind: "constructor", id: ID }, new Map()), "constructor");
  assert.equal(stateWords("proposed"), "waiting for your answer");
  assert.equal(stateWords("accepted"), "in effect");
  assert.equal(stateWords("superseded"), "corrected");
  assert.equal(stateWords("__proto__"), "proto");
});

test("evidence links: own pages only, UUIDs only", () => {
  const P = "99999999-9999-4999-8999-999999999999";
  assert.equal(evidenceHref(P, { kind: "experiment", id: ID }), `/projects/${P}/experiments/${ID}`);
  assert.equal(evidenceHref(P, { kind: "model_version", id: ID }), `/projects/${P}/models/${ID}`);
  assert.equal(evidenceHref(P, { kind: "decision_record", id: ID }), `/projects/${P}/decisions?record=${ID}`);
  assert.equal(evidenceHref(P, { kind: "candidate", id: ID }), null);
  assert.equal(evidenceHref(P, { kind: "__proto__", id: ID }), null);
  assert.equal(evidenceHref(P, { kind: "experiment", id: "../x" }), null);
  assert.equal(evidenceHref("nope", { kind: "experiment", id: ID }), null);
});

const BANNED = /\b(release|champion|ref|digest|holdout|manifest|package|slot|tick|revision|window|proposal|supersede)s?\b/i;
const P = "00000000-0000-4000-8000-0000000000bb";
const MV = "00000000-0000-4000-8000-0000000000aa";
const ev = (over: Partial<ActivityEventLike>): ActivityEventLike => ({
  id: `x:${ID}`, kind: "dataset_uploaded", occurred_at: "2026-10-05T09:00:00Z", status: "training", actor: { kind: "person", is_you: true },
  subject: { kind: "dataset_version", id: ID }, link: { kind: "dataset_version", id: ID }, ...over,
});

test("UI-E: approving a model is undone from Model → Approved for scoring; other version changes keep their note", () => {
  assert.deepEqual(correctionFor("champion_promoted", "accepted"), { ...APPROVALS_SHORTCUT });
  // Only champion_promoted records an approval for scoring; the first versions in use and other moves keep their note.
  assert.deepEqual(correctionFor("ref_initialized", "accepted"), { kind: "note", text: "Change the version in use again; that adds a new entry." });
  assert.equal(correctionFor("ref_moved", "accepted").kind, "note");
  assert.match(APPROVALS_SHORTCUT.text, /earlier approved model, when there is one/);
  assert.equal(correctionFor("champion_promoted", "proposed").kind, "answer");
  assert.equal(correctionFor("champion_promoted", "superseded").kind, "none");
  assert.equal(approvalsHref(P), `/projects/${P}/models?tab=approved`);
  assert.equal(approvalsHref("not-a-uuid"), null);
});

test("UI-E + D5c: the six event kinds become plain sentences with a link; anything else is not a log row", () => {
  const names = new Map([[`experiment:${ID}`, "Run 3"], [`model_version:${MV}`, "Model v2"]]);
  const up = eventRow(ev({}), P, names)!;
  assert.deepEqual([up.who, up.whoKind, up.what], ["You", "human", "Uploaded a training file"]);
  assert.equal(up.href, `/projects/${P}/data/${ID}`);
  assert.equal(eventRow(ev({ status: "scoring", actor: { kind: "person", is_you: false } }), P, names)!.what, "Uploaded a file to score");
  assert.equal(eventRow(ev({ status: "scoring", actor: { kind: "person" } }), P, names)!.who, "A person");
  assert.equal(eventRow(ev({ actor: { kind: "agent" } }), P, names)!.who, "A connected tool (access token)");
  assert.equal(eventRow(ev({ status: "__proto__" }), P, names)!.what, "Uploaded a file");
  const ft = eventRow(ev({ kind: "final_test_run", status: null, actor: { kind: "rule" }, subject: { kind: "experiment", id: ID }, link: { kind: "experiment", id: ID } }), P, names)!;
  assert.equal(ft.what, "Scored the final test set once for Run 3");
  // A run the lineage does not name is "a run", never a short id.
  const anon = eventRow(ev({ kind: "final_test_run", actor: { kind: "rule" }, subject: { kind: "experiment", id: MV }, link: { kind: "experiment", id: MV } }), P, names)!;
  assert.equal(anon.what, "Scored the final test set once for a run");
  assert.equal(anon.linkLabel, "Open the run");
  assert.doesNotMatch(anon.what, /[0-9a-f]{8}/);
  assert.equal(ft.undo, "Cannot be repeated for this run.");
  assert.equal(ft.href, `/projects/${P}/experiments/${ID}`);
  assert.doesNotMatch(ft.what, /\d\.\d/); // no final test value
  const drift = eventRow(ev({ kind: "drift_alert", status: "alert", actor: { kind: "rule" }, subject: { kind: "model_version", id: MV }, link: { kind: "monitoring_window", id: ID } }), P, names)!;
  assert.match(drift.what, /^Weekly monitoring marked a week as alert for Model v2: drift, or real outcomes below a floor$/);
  assert.equal(drift.href, `/projects/${P}/monitoring`);
  assert.equal(drift.who, "The rules");
  assert.match(eventRow(ev({ kind: "drift_alert", subject: { kind: "model_version", id: null } }), P, names)!.what, /for the model:/);
  const sched = (status: string | null) => eventRow(ev({ kind: "scheduled_scoring", status, actor: { kind: "rule" }, link: { kind: "scoring_schedule_run", id: ID } }), P, names)!;
  const words = ["queued", "failed_to_queue", "skipped_no_new_file", "skipped_no_model_in_use", "skipped_no_approval", "skipped_owner_not_allowed", "skipped_missed"].map((x) => sched(x).what);
  assert.equal(new Set(words).size, 7);
  assert.equal(sched("queued").what, "Weekly scoring started");
  assert.equal(sched("__proto__").what, "Weekly scoring ran");
  assert.equal(sched(null).href, `/projects/${P}/predictions`);
  for (const kind of ["decision", "run_finished", "run_queued", "agent_run_started", "__proto__"]) assert.equal(eventRow(ev({ kind }), P, names), null, kind);
  assert.equal(LOG_EVENT_KINDS.size, 6);
  // D5c: retrain events: who started it (a person, the rule's question, Automatic) and the comparison outcome, never figures.
  const rr = (status: string | null, subject = { kind: "experiment", id: ID }) => eventRow(ev({ kind: "retrain_requested", status, actor: { kind: status === "person" ? "person" : "rule", is_you: true }, subject, link: { kind: "retrain_request", id: MV } }), P, names)!;
  assert.deepEqual([rr("person").what, rr("person").who, rr("person").href, rr("person").linkLabel], ["Started a retrain on newest data", "You", `/projects/${P}/experiments/${ID}`, "Open Run 3"]);
  assert.match(rr("auto").what, /automatically \(the retrain policy is Automatic\)$/);
  const asked = rr("ask", { kind: "model_version", id: MV });
  assert.deepEqual([asked.what, asked.href, asked.linkLabel], ["Asked in the Inbox: retrain on newest data?", `/projects/${P}/monitoring`, "Open Monitoring"]);
  assert.equal(rr("__proto__").what, "Retrain on newest data");
  const rc = (status: string) => eventRow(ev({ kind: "retrain_compared", status, actor: { kind: "rule" }, subject: { kind: "experiment", id: ID }, link: { kind: "retrain_comparison", id: MV } }), P, names)!;
  assert.match(rc("compared").what, /same kind of test, latest months/);
  assert.equal(rc("not_comparable").what, "Could not compare the retrained model with the model in use");
  for (const row of [rr("person"), rr("auto"), asked, rc("compared"), rc("not_comparable")]) {
    for (const text of [row.what, row.undo, row.linkLabel ?? ""]) { assert.doesNotMatch(text, BANNED, text); assert.doesNotMatch(text, /same test|\d\.\d/, text); }
  }
  assert.equal(eventRow(ev({ actor: { kind: "robot" } }), P, names)!.who, "Someone else");
  for (const row of [up, ft, drift, ...["queued", "failed_to_queue", "skipped_missed"].map(sched)]) {
    for (const text of [row.what, row.undo, row.linkLabel ?? "", row.who]) assert.doesNotMatch(text, BANNED, text);
  }
  assert.equal(eventRow(ev({ link: { kind: "dataset_version", id: "../../x" } }), P, names)!.href, null);
});

test("UI-E: the merged log is newest first; a cut decision list hides older events; a cut activity read says from when", () => {
  const e = (id: string, at: string): EventRow => ({ id, kind: "dataset_uploaded", at, who: "You", whoKind: "human", what: "Uploaded a training file", about: null, href: null, linkLabel: null, undo: "—" });
  const ds = [{ id: "d1", recorded_at: "2026-10-05T10:00:00Z" }, { id: "d2", recorded_at: "2026-10-03T10:00:00Z" }];
  const events = [e("e1", "2026-10-06T10:00:00Z"), e("e2", "2026-10-04T10:00:00Z"), e("e3", "2026-10-01T10:00:00Z")];
  assert.deepEqual(mergeLog(ds, false, events, null).rows.map((r) => r.key), ["event:e1", "decision:d1", "event:e2", "decision:d2", "event:e3"]);
  assert.deepEqual(mergeLog(ds, true, events, null).rows.map((r) => r.key), ["event:e1", "decision:d1", "event:e2", "decision:d2"]);
  assert.equal(mergeLog(ds, false, events, null).note, null);
  const note = mergeLog(ds, false, events, "2026-10-01T10:00:00Z").note ?? "";
  assert.match(note, /listed from Thu, 1 Oct 2026, 10:00 UTC; older ones are not shown here\.$/);
  assert.doesNotMatch(note, BANNED);
  assert.equal(mergeLog([], true, events, "bad").note, null);
  assert.equal(mergeLog([], true, events, null).rows.length, 3); // nothing to cut against
});

test("UI-E: the newer-file banner states what is true and nothing more", () => {
  assert.equal(newerFileText(null), null);
  assert.equal(newerFileText({ count: 0, newest_created_at: "2026-10-05T09:00:00Z", newest: { kind: "dataset_version", id: ID } }), null);
  const one = newerFileText({ count: 1, newest_created_at: "2026-10-05T09:00:00Z", newest: { kind: "dataset_version", id: ID } }, true) ?? "";
  // No model in use (or unknown): nothing is said about scoring or Monitoring.
  for (const inUse of [false, null]) {
    const none = newerFileText({ count: 1, newest_created_at: "2026-10-05T09:00:00Z", newest: { kind: "dataset_version", id: ID } }, inUse) ?? "";
    assert.doesNotMatch(none, /keeps scoring|Monitoring/);
    assert.match(none, /Nothing is marked built on an older version/);
  }
  assert.match(one, /^A training file newer than the one this project uses was uploaded on Mon, 5 Oct 2026, 09:00 UTC\./);
  assert.match(one, /keeps scoring as before/);
  assert.match(one, /Monitoring/);
  const many = newerFileText({ count: 3, newest_created_at: "bad", newest: { kind: "dataset_version", id: ID } }) ?? "";
  assert.match(many, /^3 training files newer than the one this project uses were uploaded; the newest\./);
  for (const text of [one, many]) assert.doesNotMatch(text, /\b(stale|ref|wrong|retrain(ed)?)\b/i, text);
});
