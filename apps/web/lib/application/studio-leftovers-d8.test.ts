/**
 * D8b unit tests: fixed error sentences, Settings notifications and webhooks, operator plan / usage columns, experiments
 * (started_by, fold progress, Compare verdict pairs, rule suggestions), the alert cause (summary_code) and REPORTING words.
 */
import assert from "node:assert/strict";
import test from "node:test";

import { actionErrorSentence, apiCode, apiStatus, byCode, readErrorSentence } from "./api-error-text.ts";
import {
  KIND_LABEL, SECRET_ONCE, WEBHOOK_EVENTS, changedPreferences, deliveryBanners, deliveryErrorText, deliveryRows, eventLabel, preferenceRows,
  preferenceSaveError, preferencesReadError, shownSecret, validateWebhook, webhookError, webhookRows, webhooksReadError,
} from "./studio-notifications.ts";
import { usageCell, workspaceRows } from "./operator.ts";
import { nodeStatusText, startedByPhrase, startedByText, statusWords } from "./studio-runs.ts";
import { listProgressText, activitySentence, needsYouRow } from "./studio-home.ts";
import { parentChildPairs, verdictSentence } from "./studio-improve.ts";
import { ruleSuggestions } from "./studio-findings.ts";
import { alertCause, alertCauseWords } from "./studio-monitoring.ts";
import { driftSentence, inboxTitle } from "./studio-inbox.ts";
import { eventRow } from "./studio-history.ts";

const P = "11111111-1111-4111-8111-111111111111";
const R = "22222222-2222-4222-8222-222222222222";
const W = "33333333-3333-4333-8333-333333333333";
const SERVER = "raw server words <script>alert(1)</script>";
const err = (status: number, code = "", message = SERVER, details: unknown = {}) => ({ status, body: { error: { code, message, details } } });

test("error text: status and code only, never the server's message; own keys only", () => {
  assert.equal(apiStatus(err(409)), 409);
  assert.equal(apiStatus(new Error("x")), null);
  assert.equal(apiStatus({ status: "409" }), null);
  assert.equal(apiCode(err(409, "Webhook_Limit_Reached")), "webhook_limit_reached");
  assert.equal(apiCode({ status: 400, body: { detail: { code: "quota_exceeded", message: SERVER } } }), "quota_exceeded");
  assert.equal(apiCode({ status: 400, body: { error: { code: "has spaces and <b>" } } }), "");
  assert.equal(byCode({ a: 1 }, "__proto__"), undefined);
  assert.equal(byCode({ a: 1 }, "constructor"), undefined);
  for (const status of [undefined, 400, 401, 403, 404, 409, 412, 413, 422, 428, 429, 500, 503, 418]) {
    const e = status === undefined ? new Error(SERVER) : err(status);
    for (const text of [readErrorSentence(e, "webhooks"), actionErrorSentence(e, "adding the webhook")]) {
      assert.doesNotMatch(text, /raw server words|script/, String(status));
      assert.ok(text.length > 10);
    }
  }
  assert.match(readErrorSentence(err(404), "run"), /does not exist in the active workspace/);
  assert.match(actionErrorSentence(err(409), "saving"), /changed while you were looking/);
});

test("notifications: fixed labels per kind, unknown kinds left out, applies_to_you and banners from the read", () => {
  const read = {
    email_delivery: "off", email_verified: false,
    items: [
      { kind: "decision_needed", email_enabled: true, default: true, applies_to_you: true },
      { kind: "quota_warning", email_enabled: false, default: true, applies_to_you: false },
      { kind: "__proto__", email_enabled: true, default: true, applies_to_you: true },
      { kind: "something_new", email_enabled: true, default: true, applies_to_you: true },
    ],
  };
  const rows = preferenceRows(read);
  assert.deepEqual(rows.map((r) => r.kind), ["decision_needed", "quota_warning"]);
  assert.equal(rows[0].label, KIND_LABEL.decision_needed);
  assert.equal(rows[1].applies, false);
  assert.match(rows[1].note ?? "", /does not receive this kind/);
  assert.equal(rows[1].isDefault, false);
  // The drift kind names both causes (the same email covers a floor alert).
  assert.match(KIND_LABEL.drift_alert, /drift, or real outcomes below a floor/);
  const banners = deliveryBanners(read).map((b) => b.text);
  assert.match(banners[0], /Email is turned off on this server/);
  assert.match(banners[1], /not verified/);
  assert.match(deliveryBanners({ email_delivery: "paused", email_verified: true })[0].text, /paused/);
  assert.deepEqual(deliveryBanners({ email_delivery: "on", email_verified: true }), []);
  assert.match(deliveryBanners({ email_delivery: "__proto__", email_verified: true })[0].text, /does not recognise/);
  assert.deepEqual(changedPreferences(rows, { decision_needed: false, quota_warning: false, __proto__: true } as Record<string, boolean>), [{ kind: "decision_needed", email_enabled: false }]);
  assert.match(preferenceSaveError(err(403, "session_required")), /signed-in browser session/);
  assert.doesNotMatch(preferenceSaveError(err(500, "x")), /raw server words/);
  assert.equal(preferencesReadError(err(403)).kind, "forbidden");
  assert.equal(preferencesReadError(err(500)).kind, "error");
});

test("webhooks: plain text rows, closed delivery reasons, form checks, fixed error sentences", () => {
  const now = new Date("2026-10-17T12:00:00Z");
  const rows = webhookRows([
    { id: W, name: "Ops ‮ <b>alerts</b>", url: "https://hooks.example.com/dclab?x=1", events: ["decision_needed", "__proto__"], enabled: true, secret_version: 2, created_at: "2026-10-01T00:00:00Z", secret_rotated_at: "2026-10-10T09:00:00Z", last_delivery_at: "2026-10-16T08:00:00Z", last_delivery_outcome: "failed", last_error_code: "timeout", consecutive_failures: 1 },
    { id: R, name: "", url: "https://b.example.com", events: [], enabled: true, secret_version: 1, created_at: "x", paused_until: "2026-10-17T13:00:00Z" },
    { id: P, name: "Off", url: "https://c.example.com", events: ["drift_alert"], enabled: false, secret_version: 1, created_at: "x" },
  ], now);
  assert.ok(!rows[0].name.includes("‮"));
  assert.equal(rows[0].events, "A decision is waiting · Not recognised");
  assert.equal(rows[0].state.label, "Failing");
  assert.match(rows[0].last, /failed · The address did not answer in time\./);
  assert.match(rows[0].secret, /^Version 2, made /);
  assert.equal(rows[1].name, "(no name)");
  assert.equal(rows[1].state.label, "Paused after failures");
  assert.equal(rows[1].last, "No call yet");
  assert.equal(rows[2].state.label, "Off");
  assert.equal(deliveryErrorText("__proto__"), "Failed for a reason this page does not recognise.");
  assert.equal(deliveryErrorText(null), null);
  assert.equal(eventLabel("ping"), "Test call");
  assert.equal(eventLabel("constructor"), "Not recognised");
  const d = deliveryRows([{ id: "d1", event: "ping", attempt: 1, outcome: "delivered", status_code: 204, duration_ms: 12.4, created_at: "2026-10-16T08:00:00Z" }])[0];
  assert.deepEqual([d.event, d.outcome, d.status, d.reason, d.duration], ["Test call", "Delivered", "204", "—", "12 ms"]);
  assert.deepEqual(validateWebhook({ name: " Ops ", url: " https://hooks.example.com/x ", events: ["drift_alert", "__proto__", "decision_needed"] }), {
    ok: true, body: { name: "Ops", url: "https://hooks.example.com/x", events: ["decision_needed", "drift_alert"] },
  });
  for (const url of ["http://hooks.example.com", "https://user:pw@hooks.example.com", "https://hooks.example.com/#frag", "javascript:alert(1)", "https://a b.com"]) {
    assert.equal(validateWebhook({ name: "x", url, events: ["run_failed"] }).ok, false, url);
  }
  assert.equal(validateWebhook({ name: "", url: "https://a.example.com", events: ["run_failed"] }).ok, false);
  assert.equal(validateWebhook({ name: "x", url: "https://a.example.com", events: [] }).ok, false);
  assert.equal(WEBHOOK_EVENTS.length, 6);
  assert.match(webhookError(err(409, "webhooks_not_configured"), "adding the webhook"), /not set up on this server/);
  assert.match(webhookError(err(422, "destination_refused"), "adding the webhook"), /private or local network/);
  assert.match(webhookError(err(409, "ping_limit_reached"), "sending a test call"), /Too many test calls/);
  assert.doesNotMatch(webhookError(err(409, "__proto__"), "adding the webhook"), /raw server words/);
  assert.equal(webhooksReadError(err(403, "webhooks_not_permitted"), "webhooks").kind, "forbidden");
  assert.equal(webhooksReadError(err(500), "delivery log").kind, "error");
  // The secret is shown only with the server's prefix; a replay (null) or anything else shows nothing.
  assert.equal(shownSecret("dclab_whsec_abcdefghijklmnopqrstuvwxyz012345"), "dclab_whsec_abcdefghijklmnopqrstuvwxyz012345");
  for (const bad of [null, undefined, "", "secret", "dclab_whsec_<script>alert(1)</script>xxxxxxxxx", 42]) assert.equal(shownSecret(bad), null);
  assert.match(SECRET_ONCE, /only this once/);
});

test("operator console: plan, used / limit / percent / over exactly as sent, and the beta flag", () => {
  const usage = [
    { key: "compute_seconds_month", used: 1800, limit: 36_000, percent: 5, over: false },
    { key: "storage_bytes", used: 3 * 1024 ** 3, limit: 2 * 1024 ** 3, percent: 150, over: true },
    { key: "runs_per_day", used: 4, limit: null, percent: null, over: false },
  ];
  assert.deepEqual(usageCell(usage, "compute_seconds_month"), { text: "30 min of 10 h (5%)", over: false });
  assert.deepEqual(usageCell(usage, "storage_bytes"), { text: "3 GB of 2 GB (150%)", over: true });
  assert.deepEqual(usageCell(usage, "runs_per_day"), { text: "4 of unlimited", over: false });
  assert.deepEqual(usageCell(undefined, "runs_per_day"), { text: "Not in the read", over: false });
  assert.equal(usageCell([{ key: "compute_seconds_month", used: 30, limit: 3600, percent: 0.8, over: false }], "compute_seconds_month").text, "under 1 min of 1 h (1%)");
  assert.deepEqual(usageCell([{ key: "__proto__", used: 1, limit: 1, percent: 100, over: true }], "runs_per_day"), { text: "Not in the read", over: false });
  const [row, old] = workspaceRows([
    { id: W, name: "Acme", kind: "business", created_at: "2026-10-05T09:00:00Z", member_count: 1, project_count: 1, jobs_last_30d: 1, failed_jobs_last_24h: 0, active_last_7d: true, assistant_beta: true, plan: "beta", usage },
    { id: R, name: "Old", kind: "personal", created_at: "2026-10-05T09:00:00Z", member_count: 1, project_count: 1, jobs_last_30d: 1, failed_jobs_last_24h: 0, active_last_7d: false },
  ]);
  assert.deepEqual([row.plan, row.beta, row.storage.over, row.runs.text], ["beta", "On", true, "4 of unlimited"]);
  assert.deepEqual([old.plan, old.beta, old.compute.text], ["Not in the read", "Not in the read", "Not in the read"]);
});

test("experiments: started_by words, REPORTING is saving results (never finished), fold progress for the latest run only", () => {
  assert.deepEqual(["you", "person", "connected_tool", "assistant", "rules"].map(startedByText), ["You", "A person", "A connected tool", "The assistant (Beta)", "The rules"]);
  for (const v of [null, undefined, "", "__proto__", "agent"]) assert.equal(startedByText(v as string | null), "Not recorded");
  assert.equal(startedByPhrase("connected_tool"), " by a connected tool");
  assert.equal(startedByPhrase("constructor"), "");
  assert.equal(statusWords("REPORTING"), "Saving results…");
  assert.equal(statusWords("reporting"), "Saving results…");
  assert.equal(statusWords("COMPLETED"), "Finished");
  assert.equal(nodeStatusText("REPORTING"), "saving results…");
  assert.equal(nodeStatusText("NEEDS_INPUT"), "needs input");
  const latest = { experiment_id: R, status: "running", progress: { fold_number: 3, folds_planned: 5, model_family: "catboost", fold_completed: false } };
  assert.equal(listProgressText(R, "running", latest), "CatBoost: fold 3 of 5 in progress, 2 done");
  assert.equal(listProgressText(P, "running", latest), null); // another running row: no progress is read for it
  assert.equal(listProgressText(R, "completed", latest), null);
  assert.equal(listProgressText(R, "running", { experiment_id: R, progress: { fold_number: 7, folds_planned: 5 } }), null);
  assert.equal(listProgressText(R, "running", null), null);
});

test("compare: verdict pairs are compared runs based on another compared run; the sentence names the run it is based on", () => {
  assert.deepEqual(parentChildPairs([
    { experiment_id: P, parent_experiment_id: null }, { experiment_id: R, parent_experiment_id: P }, { experiment_id: W, parent_experiment_id: "44444444-4444-4444-8444-444444444444" },
    { experiment_id: "55555555-5555-4555-8555-555555555555", parent_experiment_id: "55555555-5555-4555-8555-555555555555" },
  ]), [{ child: R, parent: P }]);
  const v = { experiment_id: R, verdict: "within_the_noise", metric: "roc_auc", direction: "higher_is_better", folds: 5, mean_difference: 0.004, margin: 0.02, message: SERVER };
  const text = verdictSentence(v, "the run it is based on");
  assert.match(text, /than in the run it is based on\./);
  assert.match(text, /no evidence that the change helped/);
  assert.doesNotMatch(text, /raw server words|Keep the current model/);
  assert.match(verdictSentence(v), /than in the run in use\. .*Keep the current model\./);
});

test("improve: rule suggestions come from failed / warning checks with a change; the reason names the check; links are same-origin", () => {
  const findings = {
    investigated: true,
    checks: [
      { check: "class_imbalance", status: "warning", severity: "warning", message: SERVER, recommendation_kind: "class_weights" },
      { check: "duplicate_rows", status: "fail", severity: "error", message: SERVER, recommendation_kind: "deduplicate" },
      { check: "overfit_gap", status: "pass", severity: "info", message: SERVER, recommendation_kind: "regularize" },
      { check: "fold_instability", status: "fail", severity: "critical", message: SERVER, recommendation_kind: "simpler_model", evidence: { winner_family: "xgboost" } },
    ],
  };
  const list = ruleSuggestions(P, R, findings);
  assert.deepEqual(list.map((s) => s.check), ["fold_instability", "class_imbalance"]);
  assert.match(list[1].reason, /^Address the trust check: /);
  for (const s of list) {
    assert.ok(s.href.startsWith(`/projects/${P}/experiments/${R}?`) && s.href.endsWith("#branch"), s.href);
    assert.doesNotMatch(JSON.stringify(s), /raw server words/);
  }
  assert.deepEqual(ruleSuggestions("../x", R, findings), []);
  assert.deepEqual(ruleSuggestions(P, R, { investigated: false, checks: findings.checks }), []);
  assert.deepEqual(ruleSuggestions(P, R, null), []);
});

test("alert cause from summary_code: below a floor is never called drift (Inbox, Home, History)", () => {
  assert.equal(alertCause("below_floor"), "below_floor");
  assert.equal(alertCause("__proto__"), "unknown");
  assert.match(alertCauseWords("below_floor"), /below a floor set for this project \(this is not drift\)/);
  assert.doesNotMatch(alertCauseWords("below_floor"), /^drift/);
  assert.match(alertCauseWords("inputs_moved"), /^drift \(the inputs/);
  assert.equal(alertCauseWords(null), "drift, or real outcomes below a floor");
  const t = (code: string | null, tab = "needs_decision") => inboxTitle({ kind: "drift_alert", subject: { kind: "model_version", id: R }, summary: SERVER, status: "alert", tab, monitoring: { summary_code: code } });
  assert.equal(t("below_floor"), "Real outcomes below a floor on the model in use");
  assert.equal(t("scores_moved", "done"), "Drift alert on an earlier week or model");
  assert.equal(t(null), "Alert on the model in use");
  const floor = driftSentence({ tab: "needs_decision", monitoring: { window_start: "2026-10-05T00:00:00Z", summary_code: "below_floor" } });
  assert.match(floor, /fell below a precision or recall floor set for this project\. This is not drift\./);
  assert.doesNotMatch(floor, /\(drift\)/);
  assert.match(driftSentence({ tab: "needs_decision", monitoring: { summary_code: "inputs_moved" } }), /the inputs of this model look different from training \(drift\)/);
  const home = needsYouRow({ kind: "drift_alert", project_id: P, summary: SERVER, status: "alert", can_act: true, subject: { kind: "model_version", id: R }, monitoring: { summary_code: "below_floor" } }, { projectName: "Churn", runName: null });
  assert.match(home.sentence, /real outcomes fell below a floor set for this project \(this is not drift\)\.$/);
  assert.doesNotMatch(home.sentence, /raw server words/);
  const activity = activitySentence({ kind: "drift_alert", summary: SERVER, summary_code: "below_floor", actor: { kind: "rule" }, link: { kind: "monitoring_window", id: W } } as unknown as Parameters<typeof activitySentence>[0], null);
  assert.equal(activity, "Weekly monitoring marked a week as alert: real outcomes fell below a floor set for this project (this is not drift)");
  const row = eventRow({ id: "e", kind: "drift_alert", occurred_at: "2026-10-05T00:00:00Z", status: "alert", summary_code: "inputs_and_scores_moved", actor: { kind: "rule" }, subject: { kind: "model_version", id: R }, link: { kind: "monitoring_window", id: W } }, P, new Map([[`model_version:${R}`, "Model v2"]]))!;
  assert.equal(row.what, "Weekly monitoring marked a week as alert for Model v2: drift (the inputs and the scores look different from training)");
});
