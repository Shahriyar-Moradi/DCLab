import assert from "node:assert/strict";
import test from "node:test";
import {
  FLOORS_NOT_DRIFT, NOT_CARRIED_FIXED, NOT_COMPARABLE_REASONS, OWN_FINAL_TEST, VALUES_UNAVAILABLE, NOT_SAME_DATA, POLICIES, REFUSAL_OTHER, RULE_LIMIT, SWITCH_IS_YOURS, THRESHOLDS_APPLY, WORDING, alertFact, boundsText,
  canOfferRetrain, comparisonView, draftFrom, floorLines, isPolicy, mapRetrainError, notComparableWords, policyFacts, policyWords, realOutcomesText,
  retrainConfirmLines, retrainSteps, setByText, thresholdChanges, usedText, validateThresholds, valuesText,
  type Bounds, type RetrainStatusLike, type ThresholdValues,
} from "./studio-retrain.ts";
import { actionRoute, approvalNote, inboxTitle, outcomeWords, retrainItemSentence } from "./studio-inbox.ts";

const BANNED = /\b(release|champion|ref|digest|holdout|manifest|package|slot|tick|revision|window|proposal)s?\b/i;
const SAME_TEST = /same test/i;
const ME = "11111111-1111-4111-8111-111111111111";
const DS = "22222222-2222-4222-8222-222222222222";
const RUN = "33333333-3333-4333-8333-333333333333";
const B: Bounds = { psi_watch_min: 0.05, psi_watch_max: 0.5, psi_alert_max: 1, floor_min: 0.01, floor_max: 0.99 };
const CUR: ThresholdValues = { psi_watch: 0.1, psi_alert: 0.2, precision_floor: null, recall_floor: null };
const err = (status: number, code: string) => ({ status, body: { error: { code, message: "SERVER INTERNAL WORDS", retryable: false, details: {} } } });
const st = (over: Partial<RetrainStatusLike> = {}): RetrainStatusLike => ({ policy: "off", state: "idle", latest_window_start: "2026-10-05T00:00:00Z", newer_dataset_id: null, ...over });
const said: string[] = [];
const keep = (s: string) => { said.push(s); return s; };
const COMPARED = {
  status: "compared", reason_code: null, time_column: "signup<b>_date", incumbent_training_end: "2026-06-30", final_test_start: "2026-07-01", final_test_rows: 1200,
  challenger: { metric: "pr_auc", metric_value: 0.691, metrics: { pr_auc: 0.691, precision: 0.59, recall: 0.81 }, decision_threshold: 0.42, n_rows: 1200 },
  incumbent: { metric: "pr_auc", metric_value: 0.6, metrics: { pr_auc: 0.6, precision: 0.48, recall: 0.8 }, decision_threshold: 0.5, n_rows: 1180 },
  proposal_refusal: null, computed_at: "2026-10-12T08:00:00Z",
};

test("retrain steps: every state reads distinctly, from the read only; unknown when the read failed; no Planned step", () => {
  const states = ["idle", "drift_found", "below_floor", "retraining", "ready_to_compare", "waiting_for_you", "switched", "kept"];
  const seen = new Set<string>();
  for (const state of states) {
    const req = state === "idle" || state === "drift_found" || state === "below_floor" ? null : { origin: "person", created_at: "2026-10-10T00:00:00Z", dataset_id: DS, experiment_id: RUN, experiment_status: state === "retraining" ? "running" : "completed" };
    const steps = retrainSteps(st({ state, request: req, comparison: ["waiting_for_you", "switched", "kept"].includes(state) ? COMPARED : null, proposal_state: state === "switched" ? "accepted" : state === "kept" ? "rejected" : state === "waiting_for_you" ? "proposed" : null }), false);
    assert.deepEqual(steps.map((s) => s.key), ["retrain", "compare", "decide"]);
    assert.ok(steps.every((s) => s.state !== "planned"));
    const line = steps.map((s) => `${s.pill}|${s.text}`).join(" / ");
    seen.add(line);
    steps.forEach((s) => { keep(s.text); keep(s.pill); keep(s.title); keep(s.who); });
  }
  assert.equal(seen.size, states.length);
  const failed = retrainSteps(undefined, true);
  assert.equal(failed[0].pill, "Unknown");
  assert.match(failed[0].text, /could not be read, so the state of this step is unknown/);
  assert.equal(retrainSteps(undefined, false)[0].text, "Loading…");
  const running = retrainSteps(st({ state: "retraining", policy: "auto", request: { origin: "auto", created_at: "x", dataset_id: DS, experiment_id: RUN, experiment_status: "queued" } }), false);
  assert.match(running[0].text, /^Started automatically \(the retrain policy is Automatic\)\./);
  assert.match(running[1].text, /only if all of them are later than its training rows: same kind of test, latest months/);
  const waiting = retrainSteps(st({ state: "waiting_for_you", comparison: COMPARED, proposal_state: "proposed" }), false);
  assert.equal(waiting[2].pill, "Waiting for you");
  assert.equal(waiting[1].pill, "Compared");
});

test("retrain steps: drift and a floor alert are named apart; asked, failed and Off say what is true", () => {
  assert.match(alertFact(st({ state: "drift_found" })) ?? "", /look different from training \(drift\)/);
  const floor = alertFact(st({ state: "below_floor" })) ?? "";
  assert.match(floor, /real outcomes fell below a floor set for this project\. This is not drift\./);
  assert.equal(alertFact(st({ state: "idle" })), null);
  const off = retrainSteps(st({ state: "below_floor", policy: "off", newer_dataset_id: DS }), false)[0];
  assert.deepEqual([off.pill, off.state], ["Off", "off"]);
  assert.match(off.text, /^Retraining on its own is off for this project\..*not drift\. A newer labelled file exists\.$/);
  const ask = retrainSteps(st({ state: "drift_found", policy: "ask", request: { origin: "ask", created_at: "x", dataset_id: DS, experiment_id: null } }), false)[0];
  assert.equal(ask.pill, "Asked you");
  assert.match(ask.text, /asked in the Inbox: “Retrain on newest data\?”\. Nothing starts until a person says yes\./);
  const failed = retrainSteps(st({ state: "drift_found", request: { origin: "person", created_at: "x", dataset_id: DS, experiment_id: RUN, experiment_status: "failed" } }), false)[0];
  assert.deepEqual([failed.pill, failed.tone], ["Did not finish", "crit"]);
  assert.match(failed.text, /The last retrain run failed\. Nothing changed/);
  const ready = retrainSteps(st({ state: "ready_to_compare", request: { origin: "person", created_at: "x", dataset_id: DS, experiment_id: RUN, experiment_status: "completed" } }), false)[1];
  assert.equal(ready.pill, "Comparing");
  const refused = retrainSteps(st({ state: "ready_to_compare", comparison: { ...COMPARED, proposal_refusal: "model_in_use_changed" } }), false)[1];
  assert.match(refused.text, /No switch decision was opened: the model in use changed after the retrain started\./);
  assert.match(retrainSteps(st({ state: "ready_to_compare", comparison: { ...COMPARED, proposal_refusal: "__proto__" } }), false)[1].text, new RegExp(REFUSAL_OTHER));
  assert.doesNotMatch(retrainSteps(st({ state: "ready_to_compare", comparison: { ...COMPARED, proposal_refusal: "SERVER WORDS" } }), false)[1].text, /SERVER/);
  const odd = retrainSteps(st({ state: "__proto__", policy: "__proto__" }), false);
  assert.equal(odd[0].pill, "Unknown");
  assert.equal(retrainSteps(st({ state: "idle", proposal_state: "accepted" }), false)[2].pill, "Switched");
  for (const s of [off, ask, failed, ready, ...odd]) { keep(s.text); keep(s.pill); }
  keep(floor);
});

test("the retrain button: a newer labelled file, a person who can start runs, nothing running", () => {
  assert.equal(canOfferRetrain(st({ newer_dataset_id: DS }), true), true);
  assert.equal(canOfferRetrain(st({ newer_dataset_id: DS }), false), false);
  assert.equal(canOfferRetrain(st({ newer_dataset_id: null }), true), false);
  assert.equal(canOfferRetrain(st({ newer_dataset_id: DS, state: "retraining" }), true), false);
  assert.equal(canOfferRetrain(st({ newer_dataset_id: DS, request: { origin: "person", created_at: "x", dataset_id: DS, experiment_status: "needs_input" } }), true), false);
  assert.equal(canOfferRetrain(undefined, true), false);
  // One retrain per model in use and file: once the model in use was retrained on the newest file, no button in any later state.
  const done = (state: string, run: string) => st({ state, newer_dataset_id: DS, model_in_use_id: ME, request: { origin: "person", created_at: "x", dataset_id: DS, incumbent_model_version_id: ME, experiment_id: RUN, experiment_status: run } });
  for (const [state, run] of [["waiting_for_you", "completed"], ["ready_to_compare", "completed"], ["kept", "completed"], ["drift_found", "failed"], ["drift_found", "cancelled"]]) {
    assert.equal(canOfferRetrain(done(state, run), true), false, `${state}/${run}`);
  }
  // A newer file than the one retrained on, or a different model in use, offers it again.
  assert.equal(canOfferRetrain({ ...done("kept", "completed"), newer_dataset_id: "44444444-4444-4444-8444-444444444444" }, true), true);
  assert.equal(canOfferRetrain({ ...done("drift_found", "failed"), model_in_use_id: "55555555-5555-4555-8555-555555555555" }, true), true);
  for (const run of ["failed", "cancelled"]) assert.match(retrainSteps(done("drift_found", run), false)[0].text, /Nothing changed: the model in use stays in use\. Upload a newer labelled file to retrain again\.$/);
  const waits = retrainSteps(st({ state: "retraining", request: { origin: "person", created_at: "x", dataset_id: DS, experiment_id: RUN, experiment_status: "needs_input" } }), false)[0];
  assert.equal(waits.text, "Started by a person. The retrain run is waiting for an answer in the Inbox before it can go on.");
  keep(waits.text);
  const lines = retrainConfirmLines({ name: "churn_oct‮.csv", rows: 4200, uploadedAt: "2026-10-12T09:30:00Z" }, ["class_weighting", "__proto__", "hyperparameters"]);
  assert.match(lines[0], /^A new run trains on churn_oct \.csv \(4,200 rows · uploaded Mon, 12 Oct 2026, 09:30 UTC\) with the same target and goal/);
  assert.equal(lines[1], OWN_FINAL_TEST);
  assert.match(OWN_FINAL_TEST, /can be compared only if that final test is the latest months of the new file and every row is later/);
  assert.match(lines.join(" "), /compares it with the model in use if they can be fairly compared \(same kind of test, latest months\), or says why not/);
  assert.equal(lines[2], "Not carried over (the new run decides them again on its own rows): class weighting, model settings.");
  assert.ok(lines.some((l) => /Switching is your choice/.test(l)));
  assert.match(retrainConfirmLines(null)[0], /the newest labelled file/);
  assert.ok(retrainConfirmLines(null).includes(NOT_CARRIED_FIXED), "unknown branch settings: the fixed sentence");
  assert.ok(!retrainConfirmLines(null, []).includes(NOT_CARRIED_FIXED), "known and empty: nothing to say");
  [...lines].forEach(keep);
});

test("comparison: side by side only when compared; same kind of test, latest months; never 'same test'; untrusted text is plain", () => {
  const view = comparisonView(COMPARED);
  assert.equal(view.kind, "compared");
  if (view.kind !== "compared") return;
  assert.deepEqual(view.rows[0], { label: "PR AUC (higher is better)", retrained: "0.691", inUse: "0.600" });
  assert.deepEqual(view.rows.map((r) => r.label), ["PR AUC (higher is better)", "Precision", "Recall", "Threshold (each model's own)", "Final-test rows scored"]);
  assert.deepEqual(view.rows[3], { label: "Threshold (each model's own)", retrained: "0.42", inUse: "0.50" });
  assert.deepEqual(view.rows[4], { label: "Final-test rows scored", retrained: "1,200", inUse: "1,180" });
  assert.match(view.facts[0], /every one later than the training rows of the model in use \(by signup<b>_date: the final test starts 2026-07-01; the model in use was trained on rows up to 2026-06-30\)/);
  assert.match(view.facts[1], /same kind of test, latest months/);
  view.facts.forEach(keep);
  // A missing side shows a dash, never an invented number; an error metric says lower is better.
  const half = comparisonView({ ...COMPARED, challenger: { metric: "rmse", metric_value: 2.5 }, incumbent: null });
  if (half.kind === "compared") {
    assert.deepEqual(half.rows[0], { label: "RMSE (lower is better)", retrained: "2.500", inUse: "—" });
    assert.ok(half.rows.every((r) => r.inUse === "—"));
  } else assert.fail("compared");
  assert.deepEqual(comparisonView(null), { kind: "none" });
  assert.deepEqual(comparisonView(COMPARED, true), { kind: "withheld" });
  assert.equal(WORDING, "Same kind of test, latest months");
  assert.equal(NOT_SAME_DATA, "Not the same data");
});

test("comparison: every not-comparable reason has its own plain sentence; unknown codes fall back; no figures are put beside", () => {
  assert.equal(NOT_COMPARABLE_REASONS.length, 12);
  const words = NOT_COMPARABLE_REASONS.map((code) => {
    const v = comparisonView({ status: "not_comparable", reason_code: code, challenger: null, incumbent: null });
    assert.equal(v.kind, "not_comparable");
    return v.kind === "not_comparable" ? keep(v.reason) : "";
  });
  assert.equal(new Set(words).size, 12);
  assert.ok(words.every((w) => w.startsWith("Not comparable: ") && w.endsWith(".")));
  assert.equal(words[0], "Not comparable: the new final test overlaps the training period of the model in use.");
  for (const code of ["__proto__", "constructor", "brand_new", null]) assert.equal(notComparableWords(code), "Not comparable: the comparison could not be computed");
  const own = comparisonView({ status: "not_comparable", reason_code: "final_test_overlaps_training", challenger: { metric: "pr_auc", metric_value: 0.7, n_rows: 900 }, incumbent: { metric: "pr_auc", metric_value: 0.6 } });
  if (own.kind === "not_comparable") {
    assert.equal(own.own, "The retrained model's own final test: PR AUC 0.700 on 900 rows. The model in use was not scored on these rows, so nothing is put beside it.");
    assert.doesNotMatch(own.own ?? "", /0\.600/);
    keep(own.own ?? "");
  } else assert.fail("not comparable");
  const bare = comparisonView({ status: "not_comparable", reason_code: "time_missing" });
  assert.equal(bare.kind === "not_comparable" ? bare.own : "x", null);
});

test("real outcomes beside: labelled not the same data; nothing measured says so", () => {
  assert.equal(realOutcomesText(null), "No real outcomes of the model in use have been measured yet.");
  const text = realOutcomesText({ window_start: "2026-10-05T00:00:00Z", precision: 0.481, recall: 0.8, n_labelled: 510 });
  assert.equal(text, "Real outcomes of the model in use in the week of Mon, 5 Oct 2026 (UTC): precision 0.48, recall 0.80 on 510 rows with a real outcome. These are scored rows of the model in use, not the rows above.");
  assert.match(realOutcomesText({ window_start: "2026-10-05T00:00:00Z" }), /: not measured\./);
  keep(text);
});

test("policy: three values in plain words; who set it, a paused automatic start and a lapsed owner, UTC; prototype keys are unknown", () => {
  assert.deepEqual(POLICIES, ["off", "ask", "auto"]);
  assert.deepEqual(POLICIES.map((p) => policyWords(p).label), ["Off", "Ask me", "Automatic"]);
  assert.match(policyWords("auto").text, /never switches the model in use/);
  assert.equal(policyWords("__proto__").label, "Unknown");
  assert.equal(isPolicy("auto"), true);
  assert.equal(isPolicy("__proto__"), false);
  assert.deepEqual(policyFacts({ policy: "off", version: 0 }, ME), ["Never changed: Off is the default."]);
  const facts = policyFacts({ policy: "auto", version: 3, updated_at: "2026-10-12T09:30:00Z", updated_by_user_id: ME, owner_can_start: false, auto_paused_until: "2026-10-13T09:30:00Z" }, ME);
  assert.equal(facts[0], "Set by you on Mon, 12 Oct 2026, 09:30 UTC (version 3).");
  assert.match(facts[1], /can no longer start runs, so nothing starts automatically/);
  assert.equal(facts[2], "A plan limit refused the last automatic start, so no automatic retrain starts before Tue, 13 Oct 2026, 09:30 UTC. Saving the policy clears this.");
  assert.equal(policyFacts({ policy: "ask", version: 2, updated_by_user_id: "x" }, ME)[0], "Set by a person (version 2).");
  assert.equal(policyFacts({ policy: "off", version: 2, auto_paused_until: "2026-10-13T09:30:00Z" }, ME).length, 1, "a pause only matters under Automatic");
  [...facts, RULE_LIMIT, SWITCH_IS_YOURS, ...POLICIES.map((p) => policyWords(p).text)].forEach(keep);
});

test("thresholds: values within the server's bounds; floors may be off; a reason is required; unchanged values are refused", () => {
  const d = { ...draftFrom(CUR), rationale: "Seasonal data" };
  assert.deepEqual(draftFrom({ ...CUR, precision_floor: 0.6 }).precision_on, true);
  assert.deepEqual(validateThresholds({ ...d, psi_watch: "0.15" }, B, CUR), { ok: true, values: { psi_watch: 0.15, psi_alert: 0.2, precision_floor: null, recall_floor: null } });
  const bad = (patch: Partial<typeof d>) => { const r = validateThresholds({ ...d, ...patch }, B, CUR); return r.ok ? {} : r.errors; };
  assert.match(bad({ psi_watch: "0.04" }).psi_watch ?? "", /from 0.05 to 0.5/);
  assert.match(bad({ psi_watch: "0.51" }).psi_watch ?? "", /from 0.05 to 0.5/);
  assert.ok(bad({ psi_watch: "0.05", psi_alert: "0.06" }).psi_alert === undefined);
  assert.equal(bad({ psi_watch: "0.3", psi_alert: "0.3" }).psi_alert, "The alert line must be above the watch line.");
  assert.match(bad({ psi_alert: "1.01" }).psi_alert ?? "", /at most 1/);
  for (const text of ["1e-1", "-0.1", "0.1.2", "", " ", "abc", "Infinity", "NaN", "0x1"]) assert.ok(bad({ psi_watch: text }).psi_watch, text);
  assert.match(bad({ precision_on: true, precision_floor: "0" }).precision_floor ?? "", /from 0.01 to 0.99, or switch this floor off/);
  assert.ok(bad({ recall_on: true, recall_floor: "1" }).recall_floor);
  assert.equal(bad({ recall_on: false, recall_floor: "junk" }).recall_floor, undefined, "an off floor is not read");
  assert.deepEqual(validateThresholds({ ...d, recall_on: true, recall_floor: "0.99" }, B, CUR), { ok: true, values: { ...CUR, recall_floor: 0.99 } });
  assert.equal(bad({ rationale: "   " }).rationale, "Give a reason; it is saved in History.");
  assert.ok(bad({ rationale: "x".repeat(2001) }).rationale);
  assert.equal(bad({}).form, "These are already the values in force. Change at least one.");
  assert.deepEqual(thresholdChanges(CUR, { ...CUR, psi_watch: 0.15, precision_floor: 0.6 }), ["Watch line (PSI): 0.1 → 0.15", "Precision floor: off → 0.6"]);
  assert.equal(boundsText(B), "Watch line: from 0.05 to 0.5. Alert line: above the watch line, at most 1. Each floor: from 0.01 to 0.99 (for example 0.6 means 60%), or off.");
  assert.equal(valuesText({ ...CUR, recall_floor: 0.5 }), "watch 0.1 · alert 0.2 · precision floor off · recall floor 0.5");
  [boundsText(B), THRESHOLDS_APPLY, FLOORS_NOT_DRIFT].forEach(keep);
});

test("thresholds: who set them and from when (UTC); the values each week used; floors per week, gate-aware, never drift", () => {
  assert.equal(setByText({ version: 0 }, ME), "DCLab defaults: never changed for this project.");
  const tail = "Weeks computed with new scored data after that use these values; past weeks, and a week recomputed without new scorings, keep the values they used.";
  assert.equal(setByText({ version: 4, changed_at: "2026-10-12T09:30:00Z", changed_by_user_id: ME }, ME), `Set by you, version 4 on Mon, 12 Oct 2026, 09:30 UTC. ${tail}`);
  assert.equal(setByText({ version: 4, changed_at: null, changed_by_user_id: null }, ME), `Set, version 4. ${tail}`);
  assert.match(THRESHOLDS_APPLY, /^Weeks computed with new scored data after you save use the new values; past weeks, and a week recomputed without new scorings, keep the values they used\./);
  assert.match(setByText({ version: 2, changed_by_user_id: "someone" }, ME), /^Set by a person/);
  assert.equal(usedText({ version: 1, psi_watch: 0.15, psi_alert: 0.3, precision_floor: 0.6 }, null), "watch 0.15 · alert 0.3 · precision floor 0.6 · recall floor off");
  assert.equal(usedText(null, { ...CUR, precision_floor: 0.5 }), "defaults (watch 0.1 · alert 0.2 · precision floor off · recall floor off)");
  assert.equal(usedText(null, null), VALUES_UNAVAILABLE);
  const lines = floorLines({
    precision: { floor: 0.6, value: 0.48, status: "below", successes: 48, trials: 100 },
    recall: { floor: 0.5, value: null, status: "not_judged", reason_code: "too_few_positive_outcomes" },
  });
  assert.equal(lines[0], "Precision floor (60%): real precision 48% is below it, clearly so for this many rows. This makes the week an alert; it is not drift.");
  assert.equal(lines[1], "Recall floor (50%) is not judged: too few labelled rows had the outcome.");
  assert.match(floorLines({ precision: { floor: 0.6, value: 0.55, status: "not_judged", reason_code: "within_chance" } })[0], /within what chance allows.*\(real precision 55%\)/);
  assert.match(floorLines({ recall: { floor: 0.5, status: "not_judged", reason_code: "__proto__" } })[0], /not judged until enough real outcomes are linked/);
  assert.equal(floorLines({ precision: { floor: 0.6, value: 0.7, status: "ok" } })[0], "Precision floor (60%): real precision 70% is at or above it.");
  for (const junk of [null, [], "x", { precision: null }, { precision: { floor: "a" } }, { __proto__: { floor: 0.5 } }]) assert.deepEqual(floorLines(junk), [], JSON.stringify(junk));
  lines.forEach(keep);
});

test("errors: fixed plain sentences by code, then status; never the server's message; a lost answer claims nothing", () => {
  const codes: Array<[string, number, string]> = [
    ["start", 409, "retrain_in_progress"], ["start", 409, "dataset_not_newer"], ["start", 409, "dataset_already_retrained"], ["start", 404, "dataset_not_found"],
    ["start", 409, "dataset_not_in_project"], ["start", 409, "no_model_in_use"], ["start", 429, "run_quota_exceeded"], ["thresholds", 409, "thresholds_unchanged"],
    ["decide", 409, "invalid_decision_transition"], ["policy", 403, "human_session_required"], ["policy", 409, "idempotency_key_conflict"],
  ];
  const titles = codes.map(([action, status, code]) => {
    const e = mapRetrainError(err(status, code), action as "start");
    assert.doesNotMatch(`${e.title} ${e.detail}`, /SERVER/);
    keep(e.title); keep(e.detail);
    return e.title;
  });
  assert.equal(new Set(titles).size, titles.length, "each code has its own title");
  assert.match(mapRetrainError(err(403, "forbidden"), "policy").detail, /Only workspace owners and administrators can change the retrain policy/);
  assert.match(mapRetrainError(err(403, ""), "thresholds").detail, /Only workspace owners and administrators can change the thresholds/);
  assert.match(mapRetrainError(err(403, ""), "start").detail, /role that can run ML work/);
  assert.equal(mapRetrainError(err(412, "precondition_failed"), "thresholds").title, "This changed while you were looking");
  assert.equal(mapRetrainError(err(428, "precondition_required"), "policy").title, "This changed while you were looking");
  assert.match(mapRetrainError(err(422, "validation_failed"), "thresholds").detail, /limits shown/);
  assert.equal(mapRetrainError(err(429, "usage_limit"), "start").title, "A limit was reached");
  assert.equal(mapRetrainError(err(409, "__proto__"), "policy").title, "The retrain policy was refused");
  assert.equal(mapRetrainError(err(409, "thresholds_unchanged"), "policy").title, "The retrain policy was refused", "codes are per action");
  for (const e of [err(500, "internal"), new Error("network"), null, { status: 502 }]) {
    const m = mapRetrainError(e, "start");
    assert.equal(m.title, "Could not confirm the retrain");
    assert.match(m.detail, /may have been saved/);
  }
});

test("inbox: the retrained model's Switch / Keep are the existing approval and reject; the question goes to Monitoring", () => {
  const actions = [
    { name: "accept", operation: "POST /v1/projects/{project_id}/refs/{ref_kind}", path_params: {}, body: {}, allowed: true },
    { name: "reject", operation: "POST /v1/decisions/{decision_id}/reject", path_params: {}, body: {}, allowed: true },
  ];
  assert.equal(actionRoute({ kind: "challenger_ready", actions }, "accept"), "ref_move_accept");
  assert.equal(actionRoute({ kind: "challenger_ready", actions }, "reject"), "decision_reject");
  assert.equal(actionRoute({ kind: "challenger_ready", actions: [{ ...actions[0], operation: "POST /v1/decisions/{decision_id}/accept" }] }, "accept"), null);
  assert.equal(actionRoute({ kind: "retrain_ask", actions: [{ name: "retrain", operation: "POST /v1/projects/{project_id}/retrain", path_params: {}, body: {}, allowed: true }] }, "retrain"), null);
  const t = (kind: string, status: string, tab = "needs_decision") => inboxTitle({ kind, subject: { kind: "model_version", id: null }, summary: "SERVER WORDS", status, tab });
  assert.equal(t("retrain_ask", "asked"), "Retrain on newest data?");
  assert.equal(t("retrain_ask", "closed", "done"), "Retrain on newest data? (answered or no longer current)");
  assert.deepEqual(["proposed", "accepted", "rejected", "__proto__"].map((s) => t("challenger_ready", s)), ["Retrained model ready to compare", "Switched to the retrained model", "Kept the model in use", "Retrained model: decided"]);
  assert.match(approvalNote({ kind: "challenger_ready" }, "ref_move_accept", "accept") ?? "", /^Switch makes the retrained model the model in use/);
  assert.match(approvalNote({ kind: "challenger_ready" }, "decision_reject", "reject") ?? "", /the model in use stays/);
  assert.equal(outcomeWords("asked"), "waiting for your answer");
  const ask = retrainItemSentence({ kind: "retrain_ask", tab: "needs_decision", status: "asked" }) ?? "";
  assert.match(ask, /The retrain policy is Ask me, so nothing started\./);
  assert.match(retrainItemSentence({ kind: "challenger_ready", tab: "needs_decision", status: "proposed" }) ?? "", /^A retrained model finished\. Monitoring shows it beside the model in use \(same kind of test, latest months\) or why they could not be compared\./);
  assert.equal(retrainItemSentence({ kind: "drift_alert", tab: "done", status: "alert" }), null);
  for (const s of [ask, retrainItemSentence({ kind: "retrain_ask", tab: "done", status: "closed" }) ?? "", retrainItemSentence({ kind: "challenger_ready", tab: "done", status: "accepted" }) ?? ""]) keep(s);
});

test("banned words and wording: no sentence says 'same test', uses internal words or leaks the server's words", () => {
  assert.ok(said.length > 60, `collected ${said.length}`);
  for (const s of said) {
    assert.doesNotMatch(s, SAME_TEST, s);
    assert.doesNotMatch(s, /SERVER|undefined|\[object|NaN/, s);
    assert.doesNotMatch(s.replace(/signup<b>_date/, ""), BANNED, s);
  }
});
