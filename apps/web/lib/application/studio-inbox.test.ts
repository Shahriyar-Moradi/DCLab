import assert from "node:assert/strict";
import test from "node:test";
import {
  actionRoute, answerLines, badgeText, comparableRunIds, disabledReason, evidenceLinks, inboxCompareHref, lookup, mapInboxError, questionHref, refVersionsFor, subjectLink, PROPOSED_BY_LABEL, type InboxItemLike,
} from "./studio-inbox.ts";

const P = "11111111-1111-4111-8111-111111111111";
const A = "22222222-2222-4222-8222-222222222222";
const B = "33333333-3333-4333-8333-333333333333";

const item = (over: Partial<InboxItemLike> = {}): InboxItemLike => ({
  id: `decision_proposal:${A}`, kind: "decision_proposal", project_id: P, status: "proposed", level: null,
  subject: { kind: "experiment", id: A }, evidence_refs: [{ kind: "experiment", id: A }, { kind: "experiment", id: B }, { kind: "agent_run", id: B }],
  actions: [{ name: "accept", operation: "POST /v1/decisions/{decision_id}/accept", path_params: { decision_id: A }, body: {}, allowed: false }],
  can_act: false, ...over,
});

test("evidence links need a UUID project and id; kinds without a page stay plain text", () => {
  const links = evidenceLinks(item());
  assert.equal(links[0].href, `/projects/${P}/experiments/${A}`);
  assert.equal(links[2].href, null);
  assert.equal(evidenceLinks(item({ project_id: "not-a-uuid" }))[0].href, null);
  assert.equal(evidenceLinks(item({ evidence_refs: [{ kind: "experiment", id: "/etc" }] }))[0].href, null);
  assert.equal(subjectLink(item())!.href, `/projects/${P}/experiments/${A}`);
  assert.equal(subjectLink(item({ subject: { kind: "project", id: null } })), null);
});

test("comparison needs two distinct runs from the item's own evidence", () => {
  assert.deepEqual(comparableRunIds(item()), [A, B]);
  assert.equal(inboxCompareHref(P, [A, B]), `/projects/${P}/experiments/compare?ids=${A},${B}`);
  assert.equal(inboxCompareHref(P, [A]), null);
  assert.equal(inboxCompareHref(P, [A, "x"]), null);
  assert.deepEqual(comparableRunIds(item({ evidence_refs: [{ kind: "experiment", id: A }] })), [A]);
});

test("question answers go to the run page", () => {
  assert.equal(questionHref(item()), `/projects/${P}/experiments/${A}`);
  assert.equal(questionHref(item({ subject: { kind: "project", id: P } })), `/projects/${P}/experiments`);
  assert.equal(questionHref(item({ project_id: null })), null);
});

test("disabled buttons say why", () => {
  assert.match(disabledReason(item(), "accept", false)!, /not decide/);
  assert.match(disabledReason(item({ kind: "agent_proposal", actions: [{ name: "accept", operation: "POST /v1/proposals/{proposal_id}/accept", path_params: {}, body: {}, allowed: false }] }), "accept", true)!, /whose conversation/);
  assert.equal(disabledReason(item({ actions: [{ ...item().actions[0], allowed: true }] }), "accept", true), null);
  assert.equal(disabledReason(item(), "revert", false), null);
});

test("write errors: owner_only is plain, 403/404/409/412/428 are mapped", () => {
  const err = (status: number, code = "", message = "") => ({ status, body: { error: { code, message } } });
  assert.match(mapInboxError(err(403, "owner_only")).title, /Only the owner/);
  assert.match(mapInboxError(err(403)).title, /cannot do this/);
  assert.match(mapInboxError(err(404)).title, /gone/);
  assert.match(mapInboxError(err(409, "invalid_decision_transition")).title, /Already decided/);
  assert.match(mapInboxError(err(412, "precondition_failed")).title, /changed/);
  assert.match(mapInboxError(err(428)).title, /out of date/);
});

test("answers are shown as plain lines, summarised when nested, never raw JSON", () => {
  const lines = answerLines({ column_role: "id\u0000", tags: ["a", "b"], nested: { x: 1, y: 2 }, rows: [{ a: 1 }], none: null, long: "x".repeat(500) });
  assert.deepEqual(lines.slice(0, 5), [
    { key: "column role", value: "id " }, { key: "tags", value: "a, b" }, { key: "nested", value: "2 fields" }, { key: "rows", value: "1 entries" }, { key: "none", value: "none" },
  ]);
  assert.equal(lines[5].value.length, 160);
  assert.deepEqual(answerLines(null), []);
  assert.ok(answerLines(Object.fromEntries(Array.from({ length: 20 }, (_, i) => [`k${i}`, i]))).length <= 8);
});

test("badge text", () => {
  assert.equal(badgeText(0), null);
  assert.equal(badgeText(undefined), null);
  assert.equal(badgeText(7), "7");
  assert.equal(badgeText(250), "99+");
});

const act = (name: string, operation: string) => ({ name, operation, path_params: {}, body: {}, allowed: true });

test("only allow-listed (kind, action, operation) triples are sent; anything else is never called", () => {
  const decision = (name: string, operation: string) => item({ actions: [act(name, operation)] });
  assert.equal(actionRoute(decision("accept", "POST /v1/decisions/{decision_id}/accept"), "accept"), "decision_accept");
  assert.equal(actionRoute(decision("accept", "POST /v1/projects/{project_id}/refs/{ref_kind}"), "accept"), "ref_move_accept");
  assert.equal(actionRoute(decision("reject", "POST /v1/decisions/{decision_id}/reject"), "reject"), "decision_reject");
  // Unknown action name on a decision (e.g. a future revert) must not fall through to accept.
  assert.equal(actionRoute(decision("revert", "POST /v1/proposals/{proposal_id}/revert"), "revert"), null);
  assert.equal(actionRoute(decision("accept", "POST /v1/decisions/{decision_id}/reject"), "accept"), null);
  assert.equal(actionRoute(decision("accept", "DELETE /v1/decisions/{decision_id}"), "accept"), null);
  const proposal = (name: string, operation: string) => item({ kind: "agent_proposal", actions: [act(name, operation)] });
  assert.equal(actionRoute(proposal("revert", "POST /v1/proposals/{proposal_id}/revert"), "revert"), "proposal_revert");
  assert.equal(actionRoute(proposal("accept", "POST /v1/proposals/{proposal_id}/reject"), "accept"), null);
  assert.equal(actionRoute(proposal("supersede", "POST /v1/decisions/{decision_id}/supersede"), "supersede"), null);
  assert.equal(actionRoute(item({ kind: "question", actions: [act("answer", "POST /v1/execution-requests/{request_id}/target-confirmation")] }), "answer"), null);
  assert.match(disabledReason(decision("accept", "POST /v1/nope"), "accept", true)!, /not available from the inbox/);
});

test("ref versions cover every ref kind, null where the project has none", () => {
  assert.deepEqual(refVersionsFor([{ ref_kind: "champion_model", version: 3 }, { ref_kind: "dataset", version: 1 }]),
    { problem_spec: null, dataset: 1, split_plan: null, feature_recipe: null, champion_model: 3 });
});

test("428 ref_versions_required, owner_only (generic) and prototype-key lookups", () => {
  const err = (status: number, code: string) => ({ status, body: { error: { code, message: "" } } });
  assert.match(mapInboxError(err(428, "ref_versions_required")).detail, /versions of the refs/);
  assert.doesNotMatch(mapInboxError(err(403, "owner_only")).detail, /accept/);
  assert.equal(lookup(PROPOSED_BY_LABEL, "agent", "x"), "agent");
  for (const key of ["__proto__", "constructor", "toString", "hasOwnProperty"]) assert.equal(lookup(PROPOSED_BY_LABEL, key, "fallback"), "fallback", key);
  assert.equal(lookup(PROPOSED_BY_LABEL, null, "fallback"), "fallback");
  assert.equal(evidenceLinks(item({ evidence_refs: [{ kind: "constructor", id: A }] }))[0].label, `constructor ${A.slice(0, 8)}`);
});
