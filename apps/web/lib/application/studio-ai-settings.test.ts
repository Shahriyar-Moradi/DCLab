import assert from "node:assert/strict";
import test from "node:test";

import {
  LEVELS_LEGEND, LEVELS_NOTE, actorWords, settingOverride, aiStatus, automaticCell, changeLine, dataWords, modelWords, parseDollars, pointName, reportTitle, settingsError, shownLevel,
  spendView, switchSubject, testedCell, withMonthlyBudget,
} from "./studio-ai-settings.ts";

const U = "11111111-1111-4111-8111-111111111111";
const BANNED = /\b(governance|incidents?|kill switch|L[0-3]|holdout|digest|jev|nooa|tenant|harness|agents?)\b/i;

const gov = (over: Record<string, unknown> = {}) => ({
  ai_enabled_setting: true, policy_unavailable: null as string | null,
  switches: { platform_ai_blocking: null as string | null, workspace: [] as Array<{ switch_key: string; state: string; held_by_incident: boolean }> }, ...over,
});

test("AI status: the first reason AI is off is named; on only when nothing says off", () => {
  assert.equal(aiStatus(gov()).on, true);
  assert.match(aiStatus(gov({ switches: { platform_ai_blocking: "global_ai", workspace: [] } })).sentence, /every workspace/);
  assert.match(aiStatus(gov({ ai_enabled_setting: false })).sentence, /server/);
  assert.match(aiStatus(gov({ policy_unavailable: "x" })).sentence, /could not be read/);
  const off = aiStatus(gov({ switches: { platform_ai_blocking: null, workspace: [{ switch_key: "all_ai", state: "off", held_by_incident: false }] } }));
  assert.deepEqual([off.on, off.workspaceOff, off.held], [false, true, false]);
  const held = aiStatus(gov({ switches: { platform_ai_blocking: null, workspace: [{ switch_key: "all_ai", state: "on", held_by_incident: true }] } }));
  assert.deepEqual([held.on, held.workspaceOff, held.held], [false, true, true]);
  assert.match(held.sentence, /problem report/);
  // The server setting is named as such (its read also sets platform_ai_blocking).
  assert.equal(aiStatus(gov({ switches: { platform_ai_blocking: "setting:AI_ENABLED", workspace: [] } })).sentence, "AI is off on this server.");
  // Switches that stop only the assistant are listed in words.
  const lead = aiStatus({ ...gov({ switches: { platform_ai_blocking: null, workspace: [{ switch_key: "agent:lead", state: "off", held_by_incident: false }, { switch_key: "provider:anthropic", state: "off", held_by_incident: false }] } }), model_in_use: { provider: "anthropic" } });
  assert.deepEqual([lead.on, lead.assistantOff], [true, ["agent:lead", "provider:anthropic"]]);
  assert.match(lead.sentence, /The assistant is off: AI helper: lead, AI provider: anthropic are switched off\./);
});

test("'Automatic' is never offered: locked where the cap would allow it later, otherwise never automatic; tied to the level in effect", () => {
  assert.deepEqual(automaticCell(3, 0), { text: "Locked until earned", locked: true });
  assert.deepEqual(automaticCell(2, 1), { text: "Locked until earned", locked: true });
  assert.deepEqual(automaticCell(1, 1), { text: "Always asks: never automatic", locked: false });
  assert.deepEqual(automaticCell(0, 0), { text: "Only recorded: never applied", locked: false });
  // Only a level really in effect (earned on the platform) says it acts on its own.
  assert.deepEqual(automaticCell(2, 2), { text: "On: it acts on its own, you can undo", locked: false });
  assert.deepEqual(automaticCell(3, 3), { text: "On: it acts on its own", locked: false });
  for (const cap of [0, 1, 2, 3]) for (const eff of [0, 1]) assert.doesNotMatch(automaticCell(cap, eff).text, /^On:|^Automatic$/);
  assert.equal(settingOverride("lead.run_experiment"), "Ask first: every assistant action is a suggestion a person accepts");
  assert.equal(settingOverride("column.semantic_role"), null);
  assert.deepEqual(LEVELS_LEGEND.map(([t]) => t), ["Only recorded", "Ask first"]);
  assert.match(LEVELS_NOTE, /locked until the assistant has earned it/);
  assert.match(LEVELS_NOTE, /read-only/);
});

test("tested cases: null or unusable is 'Not tested yet'; real numbers say they do not unlock anything", () => {
  assert.equal(testedCell(null), "Not tested yet");
  assert.equal(testedCell(undefined), "Not tested yet");
  assert.equal(testedCell({ agreement_rate: 0.9, n: 0 }), "Not tested yet");
  assert.equal(testedCell({ agreement_rate: Number.NaN, n: 10 }), "Not tested yet");
  assert.equal(testedCell({ agreement_rate: 0.9, n: 2.5 }), "Not tested yet");
  assert.equal(testedCell({ agreement_rate: 0.971, n: 846 }), 'Agreed with the rules on 97% of 846 tested cases. Not enough on its own to unlock "Automatic".');
  assert.match(testedCell({ agreement_rate: 7, n: 1 }), /100% of 1/);
});

test("levels shown are clamped to the four words; plain names, own keys only", () => {
  assert.deepEqual([shownLevel(0), shownLevel(1), shownLevel(2), shownLevel(3), shownLevel(7), shownLevel("2"), shownLevel(null)], [0, 1, 2, 3, 0, 0, 0]);
  assert.equal(pointName("column.is_identifier"), "Leave out ID-like columns");
  assert.equal(pointName("lead.run_experiment"), "Assistant: start a run");
  assert.equal(pointName("brand.new_point"), "brand new point");
  for (const key of ["__proto__", "constructor", "toString"]) assert.ok(!/function|\[object/.test(pointName(key)), key);
});

test("spend: estimate or upper bound, the month's limit, the assistant's per-answer and per-conversation limits", () => {
  const view = spendView({
    month_spend: { spent_micros: 9_400_000, calls: 120, price_is_upper_bound: true },
    spend: { workspace: [{ period: "day", limit_micros: 1, spent_micros: 0 }, { period: "month", limit_micros: 25_000_000, spent_micros: 0 }], per_run_limits_micros: { assistant_turn: 250_000, assistant_thread: 1_000_000 } },
  })!;
  assert.deepEqual(view, { spent: "$9.40", limit: "$25.00", percent: 38, qualifier: "upper bound: the model's price is not confirmed yet", calls: 120, turnLimit: "$0.25", threadLimit: "$1.00" });
  const plain = spendView({ month_spend: { spent_micros: 0, calls: 0, price_is_upper_bound: false }, spend: null, model_in_use: { model: "m", price_is_upper_bound: false } })!;
  assert.equal(plain.qualifier, "estimate from DCLab's own records");
  assert.equal(plain.limit, null);
  // The month's own flag decides: an unconfirmed model price with no such spend this month is still an estimate.
  assert.match(spendView({ month_spend: { spent_micros: 1, calls: 1, price_is_upper_bound: false }, model_in_use: { model: "m", price_is_upper_bound: true } })!.qualifier, /estimate/);
  assert.equal(spendView({}), null);
});

test("model and data words come from the read; raw rows are not a class", () => {
  assert.deepEqual(modelWords({ model: "claude-sonnet-5-5", provider: "anthropic", price_is_upper_bound: true }), {
    verb: "The model the assistant uses now", name: "Claude Sonnet 5.5", provider: "Anthropic", price: "Its price is not confirmed yet, so costs shown are upper bounds.",
  });
  assert.equal(modelWords({ model: "other<b>", provider: null, price_is_upper_bound: false })!.name, "other<b>");
  assert.equal(modelWords(null), null);
  assert.match(modelWords({ model: "claude-sonnet-5-5", price_is_upper_bound: false })!.verb, /uses now/);
  assert.match(modelWords({ model: "claude-sonnet-5-5", price_is_upper_bound: false }, false)!.verb, /would use/);
  assert.equal(dataWords({ max_class: "aggregates", sample_values_per_column: 0 }), "Column names, types and summary statistics.");
  assert.match(dataWords({ max_class: "sample_values", sample_values_per_column: 3 }), /up to 3 sample values/);
  assert.equal(dataWords({ max_class: "raw_rows", sample_values_per_column: 0 }), "Column names and types only.");
  assert.match(dataWords(null), /could not be read/);
});

test("budget: dollars parsed strictly; only workspace_month_micros changes in a deep copy", () => {
  assert.deepEqual(parseDollars("25"), { micros: 25_000_000 });
  assert.deepEqual(parseDollars("$12.50"), { micros: 12_500_000 });
  assert.deepEqual(parseDollars("0.07"), { micros: 70_000 });
  for (const bad of ["", "-1", "1e3", "12.345", "abc", "1,000", "999999.99"]) assert.ok("error" in parseDollars(bad), bad);
  const doc = { schema_version: 1, budgets: { workspace_month_micros: 25_000_000, project_month_micros: 12_000_000 }, models: { roles: {} } };
  const next = withMonthlyBudget(doc, 10_000_000)!;
  assert.equal((next.budgets as Record<string, number>).workspace_month_micros, 10_000_000);
  assert.equal((next.budgets as Record<string, number>).project_month_micros, 12_000_000);
  assert.equal(doc.budgets.workspace_month_micros, 25_000_000, "the read is not mutated");
  assert.equal(withMonthlyBudget(null, 1), null);
  assert.equal(withMonthlyBudget({ budgets: [] }, 1), null);
  assert.equal(withMonthlyBudget(JSON.parse('{"__proto__":{"budgets":{}}}'), 1), null, "inherited budgets are not read");
  assert.equal(changeLine({ path: "budgets.workspace_month_micros", before: 25_000_000, after: 10_000_000 }), "Monthly AI budget: $25.00 → $10.00");
  assert.equal(changeLine({ path: "data.max_class", before: "aggregates", after: "metadata" }), "data.max class: aggregates → metadata");
});

test("problem reports, actors and switch subjects in plain words, without identities", () => {
  assert.equal(reportTitle("budget_exhausted"), "A spending limit was reached");
  assert.equal(reportTitle("brand_new"), "brand new");
  assert.equal(reportTitle("__proto__"), "proto");
  assert.equal(actorWords("platform_staff"), "DCLab staff");
  assert.equal(actorWords("rule:retrain"), "The rules");
  assert.equal(actorWords(`user:${U}`), "A person");
  assert.equal(actorWords(`user:${U}`, U), "You");
  assert.equal(actorWords(null), "—");
  assert.equal(actorWords("withheld"), "Someone else");
  assert.equal(switchSubject("all_ai"), "All AI in this workspace");
  assert.equal(switchSubject("agent:experiment_critic"), "AI helper: experiment critic");
  assert.equal(switchSubject("constructor"), "constructor");
});

test("settings errors: fixed sentences by code then status; server text never shown", () => {
  const err = (status: number, code?: string) => ({ status, body: code ? { error: { code, message: "LEAK <b>" } } : null });
  assert.match(settingsError(err(409, "switch_held_by_incident")), /problem report is open/);
  assert.match(settingsError(err(422, "policy_cap_violation")), /more than DCLab allows/);
  assert.match(settingsError(err(409, "incident_not_open")), /already resolved/);
  assert.match(settingsError(err(403)), /owner or admin/);
  assert.equal(settingsError(err(403, "self_approval_not_allowed")), "Another owner or admin must accept a change you proposed. It is waiting in the change history.");
  for (const code of ["already_decided", "proposal_expired", "policy_digest_mismatch", "consent_change_unacknowledged"]) {
    assert.doesNotMatch(settingsError(err(409, code)), /Something changed at the same time/, code);
  }
  for (const code of ["__proto__", "constructor"]) assert.doesNotMatch(settingsError(err(500, code)), /function|LEAK/);
  for (const status of [403, 404, 409, 422, 429, 500]) assert.doesNotMatch(settingsError(err(status, "zzz")), /LEAK|<b>/);
});

test("words on the settings page avoid internal names", () => {
  const sample = [
    LEVELS_NOTE, aiStatus(gov()).sentence, aiStatus(gov({ ai_enabled_setting: false })).sentence, testedCell(null), automaticCell(2, 0).text, automaticCell(1, 1).text, ...LEVELS_LEGEND.flat(),
    reportTitle("replay_mismatch"), reportTitle("data_exposure"), pointName("ops.diagnose"), dataWords({ max_class: "aggregates", sample_values_per_column: 0 }),
    settingsError({ status: 409, body: { error: { code: "switch_held_by_incident" } } }),
  ];
  for (const text of sample) assert.doesNotMatch(text, BANNED, text);
});
