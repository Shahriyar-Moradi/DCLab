import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  BUDGET_LIMITS, DEFAULT_BUDGET_INPUT, STOP_REASON_CODES, acceptBody, acceptGate, againstName, chartRows, finalTestView, foldSummary, goalFromState, goalWords,
  improveLoopHref, improveNoticeHref, isPolled, loopError, loopStatus, loopSteps, outcomeSentence, readState, spendView, startBody, stopReasonText,
  tryBadge, tryChange, trySentence, tryVerdict, validateBudget, type LoopIteration, type LoopRead,
} from "./studio-improve-loop.ts";
import { improveItemHref, improveItemSentence, inboxTitle, outcomeWords } from "./studio-inbox.ts";
import { sealedTriesOf, triesOf } from "./studio-improve.ts";

const P = "11111111-1111-4111-8111-111111111111";
const L = "22222222-2222-4222-8222-222222222222";
const ODD = ["__proto__", "constructor", "toString", "hasOwnProperty", "nope", ""];

const folds = (probe: number[], incumbent: number[]) => probe.map((p, i) => ({ fold_number: i + 1, probe: p, incumbent: incumbent[i], difference: p - incumbent[i] }));
const it = (patch: Partial<LoopIteration>): LoopIteration => ({
  seq: 1, kind: "family_include", status: "rejected", reason: "inside_margin", action: { kind: "family_include", family: "extra_trees" },
  comparison: { goal_metric: "pr_auc", direction: "higher_is_better", folds: 5, mean_difference: 0.004, margin: 0.03, fold_scores: folds([0.61, 0.6, 0.62, 0.63, 0.6], [0.6, 0.6, 0.61, 0.62, 0.61]) },
  created_at: "2026-10-17T10:00:00Z", ...patch,
});
const loop = (patch: Partial<LoopRead>): LoopRead => ({
  id: L, project_id: P, start_experiment_id: P, final_experiment_id: null, status: "running", outcome: null, stop_reason: null, stop_detail: null,
  goal: { plain: { metric: "pr_auc" } }, budget: { max_iterations: 6, max_wall_seconds: 7200, max_training_seconds_total: 3600, max_llm_micros: 2_000_000, patience: 3 },
  validation_folds: 5, looks_before: 1, sealed_set_label: "reported_before", sealed_set_consulted: false, final_attempts: 0, accepted_iteration_seq: null,
  started_by: "person", ai: "off", training_seconds_spent: 600, llm_micros_spent: 0, created_at: "2026-10-17T10:00:00Z", started_at: "2026-10-17T10:00:00Z",
  iterations: [], actions: { accept: false, keep_current: false, cancel: true }, ...patch,
});
const apiError = (status: number, code: string, message = "SECRET server text with raw_code") => Object.assign(new Error(message), { status, body: { error: { code, message } } });

test("try verdict words: only an accepted try beats the noise; within the noise is said plainly; unknown keys are never shown raw", () => {
  assert.equal(tryBadge(tryVerdict(it({ status: "accepted", reason: null }))).label, "Beats noise");
  for (const reason of ["inside_margin", "below_minimum_effect", "no_change", "try_misses_rule"]) assert.equal(tryBadge(tryVerdict(it({ reason }))).label, "Within noise", reason);
  assert.equal(tryBadge(tryVerdict(it({ reason: "worse" }))).label, "Worse");
  assert.equal(tryBadge(tryVerdict(it({ status: "not_comparable", reason: "truncated" }))).label, "Not comparable");
  assert.match(trySentence(it({ status: "not_comparable", reason: "truncated" })), /^Not comparable: a model was skipped or cut short for time\.$/);
  // A suspicious "beat" is never a gain.
  assert.equal(tryVerdict(it({ reason: "rejected_suspicious" })), "suspicious");
  assert.doesNotMatch(tryBadge("suspicious").label, /beats/i);
  // Within the noise never claims a gain.
  const within = trySentence(it({}));
  assert.match(within, /inside the noise margin, so it could be luck/);
  assert.doesNotMatch(within, /beats|improv/i);
  assert.match(trySentence(it({ status: "accepted", reason: null })), /more than the noise margin.*evidence, not proof/);
  for (const odd of ODD) {
    assert.equal(tryVerdict(it({ status: odd })), "unknown", odd);
    assert.equal(tryBadge(tryVerdict(it({ reason: odd }))).label, "Not recognised", odd);
    const text = trySentence(it({ status: "not_comparable", reason: odd }));
    assert.equal(text, "Not comparable: the two runs cannot be compared fairly.", odd);
  }
  assert.equal(tryVerdict(it({ kind: "operating_point", status: "recorded" })), "threshold");
  assert.match(tryChange(it({})), /^Add the .+ model family$/);
  assert.doesNotMatch(tryChange(it({})), /_/);
  assert.match(tryChange(it({ kind: "operating_point", action: {} })), /no retraining/);
  assert.equal(tryChange(it({ kind: "__proto__" })), "A change this page does not recognise");
});

test("every ADR stop rule has its own plain sentence; unknown or prototype keys get the generic one", () => {
  const adr = ["cancelled", "failed", "blocking_finding", "goal_met", "no_improvement", "unstable_evidence", "comparison_bound", "budget_iterations", "budget_wall", "budget_compute", "proposer_exhausted"];
  assert.deepEqual([...STOP_REASON_CODES].sort(), [...adr].sort());
  const seen = new Set<string>();
  for (const code of adr) {
    const text = stopReasonText(code)!;
    assert.match(text, /^Stopped: /, code);
    assert.doesNotMatch(text, /_/, code);
    seen.add(text);
  }
  assert.equal(seen.size, adr.length);
  for (const odd of ODD.filter(Boolean)) assert.equal(stopReasonText(odd), "Stopped for a reason this page does not recognise.", odd);
  assert.equal(stopReasonText(null), null);
});

test("outcomes: kept current is the honest common result; nothing comparable never says 'no change beyond noise'", () => {
  assert.equal(outcomeSentence(loop({ status: "completed", outcome: "kept_current", stop_detail: null, iterations: [it({})] })), "No change beyond noise; current model kept.");
  // No try compared at all (every try failed, was stopped or could not be compared): no claim about noise.
  for (const iterations of [[], [it({ status: "failed", reason: "probe_failed" })], [it({ kind: "operating_point", status: "recorded" }), it({ status: "cancelled" })]]) {
    assert.equal(outcomeSentence(loop({ status: "completed", outcome: "kept_current", iterations })), "No try was compared, so nothing can be said about a gain; the current model is kept.");
  }
  assert.match(outcomeSentence(loop({ status: "completed", outcome: "kept_current", stop_detail: "kept_by_person" }))!, /chose to keep.*not used/);
  assert.match(outcomeSentence(loop({ status: "completed", outcome: "kept_current", stop_detail: "waiting_user_expired" }))!, /14 days/);
  assert.doesNotMatch(outcomeSentence(loop({ status: "completed", outcome: "nothing_comparable" }))!, /noise/);
  assert.match(outcomeSentence(loop({ status: "completed", outcome: "goal_met" }))!, /met on cross-validation/);
  assert.equal(outcomeSentence(loop({ outcome: null })), null);
  for (const odd of ODD.filter(Boolean)) assert.equal(outcomeSentence(loop({ status: "completed", outcome: odd })), "The loop ended with a result this page does not recognise.", odd);
  for (const odd of ODD) assert.equal(loopStatus(odd).label, "Not recognised", odd);
  assert.equal(loopStatus("waiting_user").label, "Waiting for you");
  assert.deepEqual(["queued", "running", "finalizing", "waiting_user", "completed", "__proto__"].map(isPolled), [true, true, true, false, false, false]);
});

test("budget: whole numbers within the server ceilings, patience at most the tries; constants equal the OpenAPI schema", () => {
  const ok = validateBudget(DEFAULT_BUDGET_INPUT);
  assert.deepEqual(ok, { ok: true, budget: { max_iterations: 6, max_wall_seconds: 7200, patience: 3 } });
  assert.deepEqual(validateBudget({ tries: "20", minutes: "720", patience: "20" }), { ok: true, budget: { max_iterations: 20, max_wall_seconds: 43200, patience: 20 } });
  const bad = (input: Partial<typeof DEFAULT_BUDGET_INPUT>) => { const r = validateBudget({ ...DEFAULT_BUDGET_INPUT, ...input }); assert.equal(r.ok, false); return (r as { errors: Record<string, string> }).errors; };
  assert.match(bad({ tries: "21" }).tries, /from 1 to 20/);
  assert.match(bad({ tries: "0" }).tries, /from 1 to 20/);
  assert.match(bad({ tries: "1.5" }).tries, /whole number/);
  assert.match(bad({ tries: "" }).tries, /whole number/);
  assert.match(bad({ tries: "-1" }).tries, /whole number/);
  assert.match(bad({ minutes: "721" }).minutes, /from 1 to 720/);
  assert.match(bad({ patience: "4", tries: "3" }).patience, /cannot be more than the most tries/);
  const openapi = JSON.parse(readFileSync(new URL("../infrastructure/v1/openapi.json", import.meta.url), "utf8")) as { components: { schemas: Record<string, { properties: Record<string, { default?: number; maximum?: number; minimum?: number }> }> } };
  const b = openapi.components.schemas.ImproveBudget.properties;
  assert.deepEqual([BUDGET_LIMITS.tries.default, BUDGET_LIMITS.tries.min, BUDGET_LIMITS.tries.max], [b.max_iterations.default, b.max_iterations.minimum, b.max_iterations.maximum]);
  assert.deepEqual([BUDGET_LIMITS.minutes.default * 60, BUDGET_LIMITS.minutes.min * 60, BUDGET_LIMITS.minutes.max * 60], [b.max_wall_seconds.default, b.max_wall_seconds.minimum, b.max_wall_seconds.maximum]);
  assert.deepEqual([BUDGET_LIMITS.patience.default, BUDGET_LIMITS.patience.min, BUDGET_LIMITS.patience.max], [b.patience.default, b.patience.minimum, b.patience.maximum]);
  // A fresh split is never sent.
  assert.ok(ok.ok);
  const body = startBody(P, { plain: { metric: "pr_auc" } }, ok.budget);
  assert.equal(Object.hasOwn(body.start, "fresh_split"), false);
  assert.equal(Object.hasOwn(body.start, "validation_folds"), false);
});

test("errors: fixed sentences by code, then by status; the server's message is never shown", () => {
  const cases: Array<[number, string, RegExp]> = [
    [409, "fresh_split_unavailable", /fresh split is not available yet/],
    [403, "token_cannot_cancel", /cannot be stopped from here/],
    [409, "not_waiting_for_a_person", /no longer waiting for you/],
    [409, "nothing_to_try", /Nothing to try/],
    [422, "goal_needs_constraint", /needs a rule/],
    [422, "goal_constraint_unsupported", /share of rows flagged/],
    [412, "precondition_failed", /changed since you loaded it/],
    [428, "precondition_required", /version was missing/],
    [429, "improve_run_quota", /Too many loops are running/],
    [429, "improve_rate_limited", /started recently/],
    [403, "human_session_required", /Only a person/],
  ];
  for (const [status, code, pattern] of cases) {
    const e = loopError(apiError(status, code));
    assert.match(`${e.title}. ${e.detail}`, pattern, code);
    assert.doesNotMatch(`${e.title} ${e.detail}`, /SECRET|raw_code/, code);
  }
  for (const odd of ODD) {
    const e = loopError(apiError(429, odd));
    assert.equal(e.title, "Too many requests", odd);
  }
  assert.equal(loopError(apiError(412, "")).title, "The loop changed since you loaded it");
  assert.equal(loopError(apiError(428, "")).title, "The loop's version was missing");
  assert.equal(loopError(apiError(418, "__proto__")).title, "Something went wrong");
  assert.equal(loopError(new TypeError("fetch failed: SECRET")).title, "Could not reach DCLab");
  assert.deepEqual([403, 404, 500].map((s) => readState(apiError(s, "x"))), ["forbidden", "not_found", "error"]);
  assert.equal(readState(new Error("x")), "error");
});

test("accept gating follows the read's actions and only tries the server accepted; a reason is required to accept", () => {
  const accepted = it({ seq: 2, status: "accepted", reason: null });
  const rejected = it({ seq: 1 });
  const waiting = loop({ status: "waiting_user", iterations: [rejected, accepted], actions: { accept: true, keep_current: true, cancel: true } });
  assert.deepEqual(acceptGate(waiting), { canAccept: true, canKeep: true, acceptable: [2], best: null, note: null });
  // A viewer (no actions) sees neither button, and a sentence why.
  const viewer = acceptGate({ ...waiting, actions: { accept: false, keep_current: false, cancel: false } });
  assert.equal(viewer.canAccept, false);
  assert.equal(viewer.canKeep, false);
  assert.match(viewer.note!, /read this loop only/);
  // Not waiting: nothing to decide, whatever the iterations say.
  assert.equal(acceptGate({ ...waiting, status: "running" }).canAccept, false);
  assert.equal(acceptGate({ ...waiting, status: "running" }).canKeep, false);
  // The server says accept but no try is accepted: keep only.
  const keepOnly = acceptGate({ ...waiting, iterations: [rejected] });
  assert.equal(keepOnly.canAccept, false);
  assert.equal(keepOnly.canKeep, true);
  // A retry after a final run that stopped before its look names the same try.
  const other = it({ seq: 3, status: "accepted", reason: null });
  assert.deepEqual(acceptGate({ ...waiting, iterations: [accepted, other], accepted_iteration_seq: 3 }).acceptable, [3]);
  assert.equal(acceptBody({ keep: false, seq: 2, reason: "  " }), "Say why you accept this try (it is saved with the decision).");
  assert.equal(acceptBody({ keep: false, seq: null, reason: "x" }), "Pick the try to accept.");
  assert.deepEqual(acceptBody({ keep: false, seq: 2, reason: " beats the noise on all folds " }), { iteration: 2, keep_current: false, rationale: "beats the noise on all folds" });
  assert.deepEqual(acceptBody({ keep: true, reason: "" }), { keep_current: true });
  assert.deepEqual(acceptBody({ keep: true, reason: "not worth a final run" }), { keep_current: true, rationale: "not worth a final run" });
  assert.match(acceptBody({ keep: true, reason: "x".repeat(1001) }) as string, /under 1000/);
});

test("deep links: only UUIDs, only improve_run notices", () => {
  assert.equal(improveLoopHref(P, L), `/projects/${P}/improve/${L}`);
  for (const bad of ["x", "__proto__", "", null, undefined, `${L}/../x`]) {
    assert.equal(improveLoopHref(P, bad), null, String(bad));
    assert.equal(improveLoopHref(bad, L), null, String(bad));
  }
  assert.equal(improveNoticeHref({ kind: "improve_run", project_id: P, source: { kind: "improve_run", id: L } }), `/projects/${P}/improve/${L}`);
  assert.equal(improveNoticeHref({ kind: "decision", project_id: P, source: { kind: "improve_run", id: L } }), null);
  assert.equal(improveNoticeHref({ kind: "improve_run", project_id: P, source: { kind: "__proto__", id: L } }), null);
  assert.equal(improveNoticeHref({ kind: "improve_run", project_id: P, source: null }), null);
  assert.equal(improveNoticeHref(null), null);
});

test("goal: the recorded threshold rule when it is an objective, else the ranking score; read-only words", () => {
  const objective = goalFromState({ chosen: { method: "objective", objective: { goal: "precision", constraints: [{ metric: "recall", op: ">=", value: 0.8 }, { metric: "x", op: "==", value: 1 }] } }, selectionMetric: "pr_auc" });
  assert.deepEqual(objective.goal, { objective: { maximize: "precision", constraints: [{ metric: "recall", op: ">=", value: 0.8 }] } });
  assert.equal(objective.words, "Best precision while recall at least 0.8, on cross-validation");
  const threshold = goalFromState({ chosen: { method: "threshold", objective: null }, selectionMetric: "pr_auc" });
  assert.deepEqual(threshold.goal, { plain: { metric: "pr_auc" } });
  assert.equal(threshold.words, "Best PR-AUC on cross-validation");
  assert.equal(goalFromState({ chosen: null, selectionMetric: " " }).goal, null);
  assert.equal(goalWords({}), "Goal not recorded");
  assert.equal(goalWords({ plain: { metric: "log_loss" }, target: { metric: "log_loss", value: 0.3 } }), "Best log loss on cross-validation; stop once it reaches 0.3 on cross-validation");
});

test("spend line: tries used of the budget (planned ones not counted), time, training, and AI off", () => {
  const run = loop({ iterations: [it({ seq: 1 }), it({ seq: 2, status: "running" }), it({ seq: 3, status: "planned" }), it({ seq: 0, kind: "operating_point", status: "recorded" })], status: "completed", finished_at: "2026-10-17T11:12:00Z" });
  assert.deepEqual(spendView(run, Date.parse("2026-10-17T12:00:00Z")), { tries: "2 of 6 tries", elapsed: "1 h 12 min of 2 h", training: "10 min of 1 h", ai: "AI off — $0" });
  assert.equal(spendView({ ...run, started_at: null }, 0).elapsed, "Not started yet");
});

test("the one final-test number: only after an accepted try's final run looked, labelled; never for a live or kept loop", () => {
  const done = loop({ status: "completed", outcome: "improved", sealed_set_consulted: true, final_experiment_id: L });
  assert.deepEqual(finalTestView(done, { pr_auc: 0.6123, roc_auc: 0.9 }), { state: "reported", metric: "PR-AUC", value: "0.612" });
  assert.equal(finalTestView(done, {}).state, "missing");
  assert.equal(finalTestView(done, null).state, "missing");
  assert.equal(finalTestView({ ...done, goal: { plain: { metric: "__proto__" } } }, { pr_auc: 0.6 }).state, "missing");
  assert.equal(finalTestView(loop({ status: "finalizing", final_experiment_id: L }), { pr_auc: 0.6 }).state, "running");
  assert.equal(finalTestView(loop({ status: "completed", outcome: "kept_current" }), { pr_auc: 0.6 }).state, "none");
  assert.equal(finalTestView({ ...done, sealed_set_consulted: false }, { pr_auc: 0.6 }).state, "none");
  assert.equal(finalTestView({ ...done, goal: { objective: { maximize: "precision", constraints: [] } } }, { precision: 0.7 }).state, "reported");
});

test("chart and fold summary: means and range from the frozen folds; shortfall comparisons are left out of the goal chart", () => {
  const s = foldSummary(it({}).comparison)!;
  assert.equal(s.folds, 5);
  assert.equal(s.tryLow, 0.6);
  assert.equal(s.tryHigh, 0.63);
  assert.equal(foldSummary({ fold_scores: [{ probe: 1, incumbent: 1 }] }), null);
  assert.equal(foldSummary(null), null);
  const { rows, leftOut } = chartRows(loop({ iterations: [it({ seq: 1 }), it({ seq: 2, comparison: { ...it({}).comparison!, scored_by: "constraint_shortfall" } }), it({ seq: 0, kind: "operating_point", status: "recorded" })] }));
  assert.deepEqual(rows.map((r) => r.name), ["Try 1"]);
  assert.equal(leftOut, 1);
});

test("progress steps follow the loop's state", () => {
  const states = (patch: Partial<LoopRead>) => loopSteps(loop(patch)).map((s) => s.state);
  assert.deepEqual(states({ status: "running" }), ["done", "current", "todo", "todo"]);
  assert.deepEqual(states({ status: "waiting_user" }), ["done", "done", "current", "todo"]);
  assert.deepEqual(states({ status: "finalizing" }), ["done", "done", "done", "current"]);
  assert.deepEqual(states({ status: "completed", outcome: "improved", sealed_set_consulted: true }), ["done", "done", "done", "done"]);
  assert.deepEqual(states({ status: "completed", outcome: "kept_current" }), ["done", "done", "todo", "todo"]);
  assert.deepEqual(states({ status: "completed", outcome: "kept_current", stop_detail: "kept_by_person" }), ["done", "done", "done", "todo"]);
});

const E1 = "33333333-3333-4333-8333-333333333333";
const E2 = "44444444-4444-4444-8444-444444444444";

test("after an acceptance, later tries are compared with the best try so far, never called 'the current model'", () => {
  const first = it({ seq: 1, status: "accepted", reason: null, experiment_id: E1, incumbent_experiment_id: P });
  const second = it({ seq: 2, experiment_id: E2, incumbent_experiment_id: E1 });
  const run = loop({ iterations: [first, second] });
  assert.equal(againstName(first, run), "the current model");
  assert.equal(againstName(second, run), "the best try so far (Try 1)");
  assert.equal(againstName(it({ incumbent_experiment_id: "55555555-5555-4555-8555-555555555555" }), run), "the best try so far");
  const text = trySentence(second, againstName(second, run));
  assert.match(text, /than the best try so far \(Try 1\)/);
  assert.match(text, /The best try so far \(Try 1\) stays\./);
  assert.doesNotMatch(text, /current model/);
  assert.deepEqual(chartRows(run).rows.map((r) => r.against), ["the current model", "the best try so far (Try 1)"]);
});

test("decision: the best so far (the loop's incumbent) is marked; other accepted tries were beaten by a later one", () => {
  const first = it({ seq: 1, status: "accepted", reason: null, experiment_id: E1 });
  const second = it({ seq: 2, status: "accepted", reason: null, experiment_id: E2 });
  const gate = acceptGate({ ...loop({ status: "waiting_user", iterations: [first, second], actions: { accept: true, keep_current: true, cancel: true } }), incumbent_experiment_id: E2 });
  assert.deepEqual(gate.acceptable, [1, 2]);
  assert.equal(gate.best, 2);
});

test("final run that looked but recorded no number: says so, no number and no results hint", () => {
  const run = loop({ status: "completed", outcome: "improved", sealed_set_consulted: true, final_experiment_id: L, stop_detail: "final_run_failed_after_look" });
  const view = finalTestView(run, { pr_auc: 0.7 });
  assert.equal(view.state, "no_number");
  assert.match((view as { text: string }).text, /used the final test set once but failed before a number was recorded; that look still counts/);
  assert.doesNotMatch((view as { text: string }).text, /open/i);
});

test("spend: once the loop stopped trying, its time ends at the last try (waiting for a person is not counted)", () => {
  const run = loop({ status: "waiting_user", iterations: [it({ seq: 1, finished_at: "2026-10-17T10:40:00Z" }), it({ seq: 2, finished_at: "2026-10-17T10:30:00Z" })] });
  assert.equal(spendView(run, Date.parse("2026-10-20T10:00:00Z")).elapsed, "40 min of 2 h");
  assert.equal(spendView({ ...run, status: "running" }, Date.parse("2026-10-17T10:50:00Z")).elapsed, "50 min of 2 h");
});

test("guided tab: sealed loop tries are left out of its tries (they never score the final test set)", () => {
  const runs = [
    { id: "a", status: "completed", created_at: "2026-10-17T10:00:00Z", parent_experiment_id: P, evidence_mode: "scored" },
    { id: "b", status: "completed", created_at: "2026-10-17T11:00:00Z", parent_experiment_id: P, evidence_mode: "sealed" },
    { id: "c", status: "completed", created_at: "2026-10-17T12:00:00Z", parent_experiment_id: P },
  ];
  assert.deepEqual(triesOf(runs, P).map((r) => r.id), ["c", "a"]);
  assert.equal(sealedTriesOf(runs, P), 1);
  assert.equal(sealedTriesOf(runs, null), 0);
});

test("Inbox: improve_run notices get a fixed title, sentence and the loop link (UUIDs only)", () => {
  const item = { kind: "improve_run", status: "waiting_user", project_id: P, source: { kind: "improve_run", id: L }, subject: { kind: "improve_run", id: L }, summary: "SERVER TEXT", tab: "needs_decision" };
  assert.equal(inboxTitle(item), "Improvement loop waiting for you");
  assert.equal(inboxTitle({ ...item, status: "blocked" }), "Improvement loop stopped: a trust check blocks it");
  assert.equal(inboxTitle({ ...item, status: "failed" }), "Improvement loop failed");
  assert.equal(inboxTitle({ ...item, status: "__proto__" }), "Improvement loop stopped");
  assert.equal(outcomeWords("waiting_user"), "waiting for your decision");
  assert.match(improveItemSentence(item)!, /accept it \(one final run, one final-test number\) or keep the current model/);
  assert.equal(improveItemSentence({ kind: "drift_alert", status: "alert" }), null);
  assert.equal(improveItemHref(item), `/projects/${P}/improve/${L}`);
  assert.equal(improveItemHref({ ...item, source: { kind: "improve_run", id: "x" } }), null);
  assert.equal(improveItemHref({ ...item, kind: "run_finished" }), null);
});
