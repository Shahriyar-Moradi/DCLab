import assert from "node:assert/strict";
import test from "node:test";
import {
  DRIFT_STATS_NOTE, agreementPill, driftSentence, driftStats, monitoringHref, resolverPill,
  actionRoute, answerLines, approvalNote, badgeText, comparableRunIds, disabledReason, evidenceLinks, inboxCompareHref, inboxTitle, lookup, mapInboxError, outcomeWords, questionHref, refVersionsFor, subjectLink, suggestedBy, INBOX_TABS, PROPOSED_BY_LABEL, type InboxItemLike,
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
  assert.match(mapInboxError(err(428, "ref_versions_required")).detail, /versions currently in use/);
  assert.doesNotMatch(mapInboxError(err(403, "owner_only")).detail, /accept/);
  assert.equal(lookup(PROPOSED_BY_LABEL, "agent", "x"), "connected tool (access token)");
  assert.equal(lookup(PROPOSED_BY_LABEL, "assistant", "x"), "the assistant");
  assert.equal(lookup(PROPOSED_BY_LABEL, "jev", "x"), "AI reviewer");
  for (const key of ["__proto__", "constructor", "toString", "hasOwnProperty"]) assert.equal(lookup(PROPOSED_BY_LABEL, key, "fallback"), "fallback", key);
  assert.equal(lookup(PROPOSED_BY_LABEL, null, "fallback"), "fallback");
  assert.equal(evidenceLinks(item({ evidence_refs: [{ kind: "constructor", id: A }] }))[0].label, "constructor");
});

test("evidence is named, never by a short id: runs by the caller's name, other kinds by kind", () => {
  const links = evidenceLinks(item(), (id) => (id === A ? "Run 2" : "Run 3"));
  assert.deepEqual(links.map((l) => l.label), ["Run 2", "Run 3", "AI agent run"]);
  assert.equal(evidenceLinks(item({ evidence_refs: [{ kind: "decision_record", id: A }] }))[0].label, "History entry");
  assert.equal(subjectLink(item(), () => "Run 2")!.label, "Run 2");
  for (const link of evidenceLinks(item({ evidence_refs: [{ kind: "agent_run", id: B }, { kind: "semantic_answer", id: A }] }))) assert.doesNotMatch(link.label, /[0-9a-f]{8}/);
});

test("who suggested it: honest labels, access-token tools are never the assistant", () => {
  assert.equal(suggestedBy("rule"), "Suggested by the rules");
  assert.equal(suggestedBy("agent", "decision_proposal"), "Suggested by connected tool (access token)");
  // A typed suggestion from an agent run: who started the run is unknown, so it is never "access token" nor "the assistant".
  assert.equal(suggestedBy("agent", "agent_proposal"), "Suggested by an AI agent (review run)");
  assert.equal(suggestedBy("assistant"), "Suggested by the assistant");
  assert.equal(suggestedBy("jev"), "Suggested by AI reviewer");
  assert.equal(suggestedBy("person"), "Suggested by a person");
  assert.equal(suggestedBy("run"), "Raised by the run");
  assert.equal(suggestedBy("future_kind"), "Suggested by future kind");
  assert.equal(suggestedBy("__proto__"), "Suggested by proto");
  assert.equal(suggestedBy(null), null);
  assert.notEqual(PROPOSED_BY_LABEL.agent, PROPOSED_BY_LABEL.assistant);
});

test("titles are plain questions or sentences; unknown kinds are not hidden or guessed", () => {
  const t = (over: object) => inboxTitle({ kind: "decision_proposal", subject: { kind: "experiment", id: A }, summary: "", status: "proposed", ...over } as Parameters<typeof inboxTitle>[0], "Run 3");
  assert.equal(t({ decision_type: "champion_promoted" }), "Put this model in use?");
  assert.equal(t({ decision_type: "__proto__" }), "Decision: proto");
  assert.equal(t({ decision_type: "some_new_type" }), "Decision: some new type");
  assert.equal(t({ kind: "agent_proposal", proposal_type: "SemanticReviewProposal" }), "A suggested column change");
  assert.equal(t({ kind: "agent_proposal", proposal_type: "ImprovementActionProposal" }), "A suggested improvement");
  assert.equal(t({ kind: "agent_proposal", proposal_type: "constructor" }), "A suggestion");
  assert.equal(t({ kind: "question", summary: "Run needs a split confirmation" }), "Confirm how this run's test rows are chosen");
  assert.equal(t({ kind: "question", summary: "Run needs a target column" }), "Choose the column this run predicts");
  assert.equal(t({ kind: "run_finished", summary: "Run #3 completed" }), "Run 3 finished");
  assert.equal(t({ kind: "run_finished", summary: "Run #3 failed" }), "Run 3 failed");
  for (const type of ["ref_moved", "champion_promoted", "winner_locked", "split_plan_created", "ref_initialized"]) assert.doesNotMatch(t({ decision_type: type }), /champion|ref |holdout|proposal|decision point|record/i);
});

test("outcomes and approval notes say what happens, in plain words", () => {
  assert.equal(outcomeWords("proposed"), "waiting for your decision");
  assert.equal(outcomeWords("accepted"), "approved");
  assert.equal(outcomeWords("applied"), "applied");
  assert.equal(outcomeWords("reverted"), "undone");
  assert.equal(outcomeWords("completed"), "finished");
  assert.equal(outcomeWords("constructor"), "constructor");
  const none = {};
  assert.match(approvalNote(none, "ref_move_accept", "accept")!, /which version the project uses/);
  assert.match(approvalNote(none, "decision_reject", "reject")!, /Nothing else changes/);
  assert.equal(approvalNote(none, null, "accept"), null);
  // Suggestion approvals say what the server does for that type, and nothing more.
  const accept = (proposal_type: string) => approvalNote({ proposal_type }, "proposal_accept", "accept")!;
  for (const type of ["ExperimentReviewProposal", "DatasetInvestigationProposal", "ImprovementActionProposal"]) assert.equal(accept(type), "Approving records that you read it; nothing changes.");
  assert.match(accept("ExperimentPlanProposal"), /marks the plan accepted; a run uses it later/);
  assert.match(accept("SemanticReviewProposal"), /starts a new run/);
  assert.match(accept("ToolCallProposal"), /runs this action as you/);
  assert.doesNotMatch(accept("constructor"), /starts|runs|applies/);
  assert.doesNotMatch(approvalNote(none, "proposal_revert", "revert")!, /branch/);
});

test("tabs use the v7 names", () => {
  assert.deepEqual(INBOX_TABS.map((x) => x.label), ["Needs your decision", "Applied automatically", "Done"]);
});

const BANNED = /\b(release|champion|ref|digest|holdout|manifest|package|slot|tick|revision|window|proposal)s?\b/i;

test("UI-E drift alert: a plain title per tab, the week's numbers only, fixed sentences and a Monitoring link", () => {
  const t = (tab: string) => inboxTitle({ kind: "drift_alert", subject: { kind: "model_version", id: A }, summary: "SERVER WORDS", status: "alert", tab });
  assert.equal(t("needs_decision"), "Drift alert on the model in use");
  assert.equal(t("done"), "Drift alert on an earlier week or model");
  const m = { window_start: "2026-10-05T00:00:00Z", max_psi: 0.314, prediction_psi: 0.05 };
  assert.deepEqual(driftStats(m), [
    { label: "Week (UTC)", value: "Mon, 5 Oct 2026" }, { label: "Largest input change of any column (PSI)", value: "0.31" }, { label: "Score change (PSI)", value: "0.05" },
  ]);
  assert.deepEqual(driftStats({ window_start: "bad", max_psi: Number.NaN, prediction_psi: null }), []);
  assert.deepEqual(driftStats(null), []);
  const open = driftSentence({ tab: "needs_decision", monitoring: m });
  const done = driftSentence({ tab: "done", monitoring: m });
  assert.match(open, /^Weekly monitoring marked the week of Mon, 5 Oct 2026 \(UTC\) as alert/);
  assert.match(open, /does not mean the model is wrong, and DCLab did not retrain or switch it/);
  assert.notEqual(open, done);
  assert.match(done, /an earlier week, or a model that is no longer in use/);
  assert.equal(outcomeWords("alert"), "alert");
  assert.match(DRIFT_STATS_NOTE, /column by column\) is on Monitoring/);
  assert.match(driftSentence({ tab: "done", monitoring: null }), /^Weekly monitoring marked a week as alert/);
  assert.equal(monitoringHref(P), `/projects/${P}/monitoring`);
  assert.equal(monitoringHref("../x"), null);
  assert.equal(evidenceLinks(item({ evidence_refs: [{ kind: "model_version", id: A }] }))[0].href, `/projects/${P}/models/${A}`);
  assert.equal(evidenceLinks(item({ evidence_refs: [{ kind: "model_version", id: A }] }))[0].label, "The model");
  for (const text of [t("needs_decision"), t("done"), open, done, ...driftStats(m).map((x) => x.label), DRIFT_STATS_NOTE]) assert.doesNotMatch(text, BANNED, text);
});

test("UI-E agreement pills: as recorded, 'we asked you' only while it waits, hidden when AI is off or unknown values", () => {
  assert.deepEqual(agreementPill("agree", "done", false), { label: "The rules and the AI agreed", tone: "ok" });
  assert.equal(agreementPill("disagree", "needs_decision", null)!.label, "The rules and the AI disagreed: we asked you");
  assert.equal(agreementPill("disagree", "done", false)!.label, "The rules and the AI disagreed");
  assert.equal(agreementPill("abstain", "done", false)!.tone, "gray");
  assert.equal(agreementPill("unavailable", "done", false)!.label, "No AI answer was available");
  assert.equal(agreementPill("agree", "done", true), null);
  assert.equal(agreementPill("__proto__", "done", false), null);
  assert.equal(agreementPill("constructor", "done", false), null);
  assert.equal(agreementPill(null, "done", false), null);
});

test("UI-E who closed a Done item: you, a person, the rules or an agent; nothing outside Done or when it expired", () => {
  assert.equal(resolverPill({ kind: "person", is_you: true }, "done"), "Decided by you");
  assert.equal(resolverPill({ kind: "person", is_you: false }, "done"), "Decided by a person");
  assert.equal(resolverPill({ kind: "person" }, "done"), "Decided by a person");
  assert.equal(resolverPill({ kind: "rule" }, "done"), "Settled by the rules");
  assert.equal(resolverPill({ kind: "agent" }, "done"), "Settled by an AI agent or a connected tool");
  assert.equal(resolverPill({ kind: "person", is_you: true }, "needs_decision"), null);
  assert.equal(resolverPill(null, "done"), null);
  assert.equal(resolverPill({ kind: "__proto__" }, "done"), null);
});
