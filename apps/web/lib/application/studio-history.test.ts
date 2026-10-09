import assert from "node:assert/strict";
import test from "node:test";
import { correctionFor, evidenceHref, evidenceName, stateWords, subjectName, whatWords, whoWords } from "./studio-history.ts";

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
  for (const type of ["winner_locked", "split_plan_created", "problem_spec_locked", "ref_moved", "champion_promoted", "ref_initialized", "operating_point_chosen", "proposal_accepted"]) {
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
