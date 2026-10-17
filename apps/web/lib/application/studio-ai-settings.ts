/**
 * AI settings view models (D6b, `/governance`, labelled Beta). Pure: no React, no fetch.
 *
 * Honesty rules: "Automatic" is never offered as a choice during the beta (it stays locked until earned on tested cases);
 * a point with no live test run reads "Not tested yet"; spend is an estimate, or an upper bound when the model's price is
 * not confirmed; every reason, resolution and evidence value is untrusted plain text. Errors are fixed sentences by code.
 */
import { isUuid, plainText } from "./command-search.ts";
import { aiIsOff } from "./studio-home.ts";
import { lookup } from "./studio-inbox.ts";
import { assistantSwitchesOff, costQualifier, isPlainObject, money, own, workspaceAiHeldOff } from "./studio-assistant.ts";

/* ---------- AI on or off, in words ---------- */

type StatusGovernance = Parameters<typeof aiIsOff>[0] & {
  switches: { platform_ai_blocking: string | null; workspace: Array<{ switch_key: string; state: string; held_by_incident: boolean }> };
};

export type AiStatus = { on: boolean; sentence: string; workspaceOff: boolean; held: boolean; assistantOff: string[] };

/**
 * Whether AI runs in this workspace and why not; the first reason that applies is named, then any switch that stops
 * the assistant alone (its helper, its use or its provider).
 */
export function aiStatus(g: StatusGovernance & { model_in_use?: { provider?: string | null } | null }): AiStatus {
  const all = g.switches.workspace.find((s) => s.switch_key === "all_ai");
  const held = !!all?.held_by_incident;
  const workspaceOff = workspaceAiHeldOff(g);
  const assistantOff = assistantSwitchesOff(g, g.model_in_use?.provider);
  const base = { workspaceOff, held, assistantOff };
  const blocking = g.switches.platform_ai_blocking;
  if (blocking === "setting:AI_ENABLED" || !g.ai_enabled_setting) return { on: false, sentence: "AI is off on this server.", ...base };
  if (blocking) return { on: false, sentence: "AI is off for every workspace: DCLab has not switched it on.", ...base };
  if (g.policy_unavailable) return { on: false, sentence: "The AI rules could not be read, so AI is refused until they can.", ...base };
  if (held) return { on: false, sentence: "AI is off for this workspace: an open problem report keeps it off until a person resolves it.", ...base };
  if (workspaceOff) return { on: false, sentence: "AI is off for this workspace: an owner or admin turned it off.", ...base };
  const also = assistantOff.length ? ` The assistant is off: ${assistantOff.map(switchSubject).join(", ")} ${assistantOff.length === 1 ? "is" : "are"} switched off.` : "";
  return { on: true, sentence: `AI is on for this workspace. It only suggests; a person accepts every change.${also}`, ...base };
}

/* ---------- kinds of decision (read-mostly during the beta) ---------- */

const POINT_NAMES: Record<string, string> = {
  "target.column": "Which column to predict",
  "spec.objective": "The goal and how to score it",
  "column.is_identifier": "Leave out ID-like columns",
  "column.semantic_role": "Treat a column as a number, category or date",
  "column.missing_value_action": "Fill empty values",
  "feature.leakage_suspect": "Flag a column that gives the answer away",
  "split.strategy": "Choose the test design",
  "training.families_budget": "Which model families to train",
  "experiment.review": "Review a finished run",
  "improve.next_action": "Suggest the next run",
  "ops.diagnose": "Explain a monitoring alert",
  "ops.retrain": "Retrain on newer data",
  "ops.release": "Approve a model for scoring",
  "ops.rollback": "Go back to an earlier model",
  "command.intent_route": "Understand what you asked the assistant",
  "proposal.completeness": "Check a suggestion is complete",
  "lead.create_problem_spec": "Assistant: set a goal",
  "lead.propose_problem_spec": "Assistant: suggest a goal",
  "lead.record_decision": "Assistant: add a history entry",
  "lead.request_agent_review": "Assistant: ask for a review",
  "lead.move_ref": "Assistant: change what the project uses",
  "lead.run_experiment": "Assistant: start a run",
  "lead.branch_experiment": "Assistant: start a variation of a run",
  "lead.predict": "Assistant: score new data",
  "lead.improve": "Assistant: start an improvement loop",
};

/** A decision point in plain words; an unknown key keeps its cleaned words. */
export function pointName(key: string): string {
  return lookup(POINT_NAMES, key, plainText(key.replace(/[._]/g, " "), 60) || "Unnamed kind of decision");
}

/** The level shown as the current setting, clamped to 0..3 (anything else reads as 0, "only recorded"). */
export function shownLevel(level: unknown): 0 | 1 | 2 | 3 {
  return level === 1 || level === 2 || level === 3 ? level : 0;
}

/**
 * The "Automatic" cell, from the level in effect: never a choice in the beta. When a level of 2 or 3 is in effect (earned on
 * the platform) it says so; otherwise it is locked where the hard cap would allow it later, or never automatic.
 */
export function automaticCell(cap: number, effective: number): { text: string; locked: boolean } {
  if (effective >= 3) return { text: "On: it acts on its own", locked: false };
  if (effective === 2) return { text: "On: it acts on its own, you can undo", locked: false };
  return cap >= 2
    ? { text: "Locked until earned", locked: true }
    : { text: cap === 1 ? "Always asks: never automatic" : "Only recorded: never applied", locked: false };
}

/** The "Setting now" words: assistant actions (lead.*) are always suggestions a person accepts, whatever the stored level. */
export function settingOverride(key: string): string | null {
  return key.startsWith("lead.") ? "Ask first: every assistant action is a suggestion a person accepts" : null;
}

export const LEVELS_LEGEND: ReadonlyArray<readonly [string, string]> = [
  ["Only recorded", "the AI's answer is stored next to the rule's answer and never used."],
  ["Ask first", "it waits for a person to accept it."],
];

/** "How it was earned": the tested-cases scalar, or "Not tested yet" when there is no live test run. */
export function testedCell(tested: { agreement_rate: number; n: number } | null | undefined): string {
  if (!tested || !Number.isFinite(tested.agreement_rate) || !Number.isInteger(tested.n) || tested.n <= 0) return "Not tested yet";
  const pct = Math.round(Math.min(1, Math.max(0, tested.agreement_rate)) * 100);
  return `Agreed with the rules on ${pct}% of ${tested.n.toLocaleString("en-US")} tested cases. Not enough on its own to unlock "Automatic".`;
}

export const LEVELS_NOTE =
  "During the beta the assistant only suggests; a person accepts every suggestion. \"Automatic\" stays locked until the assistant has earned it on tested cases, with the real model, real spend and no open problem reports. This table is read-only.";

/* ---------- spend, model and what AI may see ---------- */

type SpendGovernance = {
  month_spend?: { spent_micros: number; calls: number; price_is_upper_bound: boolean } | null;
  spend?: { workspace: Array<{ period: string; limit_micros: number; spent_micros: number }>; per_run_limits_micros?: Record<string, number> } | null;
  model_in_use?: { model: string; provider?: string | null; price_is_upper_bound: boolean } | null;
};

export type SpendView = { spent: string; limit: string | null; percent: number | null; qualifier: string; calls: number; turnLimit: string | null; threadLimit: string | null };

export function spendView(g: SpendGovernance): SpendView | null {
  if (!g.month_spend) return null;
  const month = g.spend?.workspace.find((p) => p.period === "month");
  const limits = g.spend?.per_run_limits_micros ?? {};
  const limit = month && month.limit_micros > 0 ? month.limit_micros : null;
  const turn = own(limits, "assistant_turn");
  const thread = own(limits, "assistant_thread");
  return {
    spent: money(g.month_spend.spent_micros),
    limit: limit ? money(limit) : null,
    percent: limit ? Math.min(100, Math.round((g.month_spend.spent_micros / limit) * 100)) : null,
    qualifier: costQualifier(g.month_spend.price_is_upper_bound),
    calls: g.month_spend.calls,
    turnLimit: typeof turn === "number" ? money(turn) : null,
    threadLimit: typeof thread === "number" ? money(thread) : null,
  };
}

const MODEL_NAMES: Record<string, string> = { "claude-sonnet-5-5": "Claude Sonnet 5.5" };
const PROVIDERS: Record<string, string> = { anthropic: "Anthropic", openai: "OpenAI", typesafe: "TypeSafe" };

/** The model the assistant really uses (from the router), with its provider and whether its price is confirmed. */
export function modelWords(model: SpendGovernance["model_in_use"], inUse = true): { name: string; provider: string | null; price: string; verb: string } | null {
  if (!model) return null;
  return {
    verb: inUse ? "The model the assistant uses now" : "The model the assistant would use (AI or the beta is off now)",
    name: lookup(MODEL_NAMES, model.model, plainText(model.model, 60)),
    provider: model.provider ? lookup(PROVIDERS, model.provider, plainText(model.provider, 30)) : null,
    price: model.price_is_upper_bound ? "Its price is not confirmed yet, so costs shown are upper bounds." : "Its price is confirmed.",
  };
}

const DATA_WORDS: Record<string, string> = {
  metadata: "Column names and types only.",
  aggregates: "Column names, types and summary statistics.",
  sample_values: "Column names, types, summary statistics and a few sample values per column.",
};

/** What the AI may see, from the policy's highest data class. Raw rows are not a class at all. */
export function dataWords(data: { max_class: string; sample_values_per_column: number } | null | undefined): string {
  if (!data) return "Not known: the AI rules could not be read.";
  const base = lookup(DATA_WORDS, data.max_class, "Column names and types only.");
  return data.max_class === "sample_values" && data.sample_values_per_column > 0 ? base.replace("a few", `up to ${data.sample_values_per_column}`) : base;
}

/* ---------- monthly budget (approvers only): propose, review the change, accept ---------- */

export const BUDGET_MAX_DOLLARS = 100_000;

/** Dollars typed by a person -> micro-dollars, or an error sentence. */
export function parseDollars(input: string): { micros: number } | { error: string } {
  const text = input.trim().replace(/^\$/, "");
  if (!/^\d{1,6}(\.\d{1,2})?$/.test(text)) return { error: "Enter an amount in dollars, like 25 or 12.50." };
  const dollars = Number(text);
  if (dollars > BUDGET_MAX_DOLLARS) return { error: "That is more than this form accepts." };
  return { micros: Math.round(dollars * 100) * 10_000 };
}

/**
 * The full policy with only the monthly workspace budget changed (the API takes whole documents and may only narrow the
 * platform's). Null when the document has no budgets object to change.
 */
export function withMonthlyBudget(document: Record<string, unknown> | null | undefined, micros: number): Record<string, unknown> | null {
  if (!isPlainObject(document)) return null;
  const budgets = own(document, "budgets");
  if (!isPlainObject(budgets)) return null;
  const copy = JSON.parse(JSON.stringify(document)) as Record<string, unknown>;
  (copy.budgets as Record<string, unknown>).workspace_month_micros = micros;
  return copy;
}

/** A reviewed change line: dollars for budget paths, plain text otherwise. */
export function changeLine(change: { path: string; before?: unknown; after?: unknown }): string {
  const value = (v: unknown) => (typeof v === "number" && /micros/.test(change.path) ? money(v) : plainText(typeof v === "object" ? JSON.stringify(v) : String(v ?? "none"), 80));
  const name = change.path === "budgets.workspace_month_micros" ? "Monthly AI budget" : plainText(change.path.replaceAll("_", " "), 80);
  return `${name}: ${value(change.before)} → ${value(change.after)}`;
}

/* ---------- problem reports and change history ---------- */

const REPORT_KINDS: Record<string, string> = {
  validator_rejections: "Many AI answers failed their checks",
  eval_failure: "The AI failed a test run",
  budget_exhausted: "A spending limit was reached",
  provider_failure: "The AI provider failed repeatedly",
  data_exposure: "AI may have been sent more than allowed",
  revert_rate: "Many accepted AI suggestions were undone",
  drift: "The AI's answers changed unusually",
  replay_mismatch: "A replayed AI run did not match the record",
  reconciliation: "AI spend did not match the provider's count",
  manual: "Reported by a person",
};
export function reportTitle(kind: string): string {
  return lookup(REPORT_KINDS, kind, plainText(kind.replaceAll("_", " "), 60) || "Problem report");
}

/** Who did something, without identities: you, a person, DCLab staff or the rules. */
export function actorWords(actor: string | null | undefined, myUserId?: string | null): string {
  if (!actor) return "—";
  if (actor === "platform_staff") return "DCLab staff";
  if (actor.startsWith("rule:")) return "The rules";
  if (actor.startsWith("user:")) return isUuid(myUserId) && actor === `user:${myUserId}` ? "You" : "A person";
  return "Someone else";
}

const CHANGE_KINDS: Record<string, string> = { policy: "AI rules", level: "What AI may do", switch: "AI on or off" };
export function changeKindWords(kind: string): string {
  return lookup(CHANGE_KINDS, kind, "Change");
}

const SWITCH_SUBJECT: Record<string, string> = { all_ai: "All AI in this workspace" };
export function switchSubject(key: string): string {
  if (Object.hasOwn(SWITCH_SUBJECT, key)) return SWITCH_SUBJECT[key];
  if (key.startsWith("agent:")) return `AI helper: ${plainText(key.slice(6).replaceAll("_", " "), 40)}`;
  if (key.startsWith("provider:")) return `AI provider: ${plainText(key.slice(9), 40)}`;
  if (key.startsWith("purpose:")) return `AI use: ${plainText(key.slice(8).replaceAll("_", " "), 40)}`;
  return plainText(key, 60);
}

/* ---------- errors of the settings commands ---------- */

const SETTINGS_ERRORS: Record<string, string> = {
  switch_held_by_incident: "AI cannot be turned back on while a problem report is open. Resolve the report first.",
  not_permitted: "Your role cannot do this. An owner or admin can.",
  forbidden: "Your role cannot do this. An owner or admin can.",
  policy_cap_violation: "That is more than DCLab allows for this workspace. Enter a smaller amount.",
  policy_invalid: "The change was not valid. Reload the page and try again.",
  policy_conflict: "The AI rules changed while you were looking. Reload the page and try again.",
  too_many_open_proposals: "Too many changes are waiting. Accept or wait for the open ones first.",
  incident_not_open: "This problem report was already resolved.",
  not_found: "This item is no longer there. Reload the page.",
  idempotency_key_conflict: "This was already sent with different values. Try again.",
  self_approval_not_allowed: "Another owner or admin must accept a change you proposed. It is waiting in the change history.",
  already_decided: "This change was already decided by someone else. Reload the page.",
  proposal_expired: "This change waited too long and expired. Propose it again.",
  policy_digest_mismatch: "This change was edited after you reviewed it. Reload the page and review it again.",
  consent_change_unacknowledged: "This change also changes what is shared for testing, which cannot be accepted here.",
  policy_unavailable: "The AI rules could not be read. Try again later.",
};

export function settingsError(error: unknown): string {
  const status = (error as { status?: unknown } | null)?.status;
  const body = (error as { body?: unknown } | null)?.body;
  const inner = isPlainObject(body) ? own(body, "error") : undefined;
  const code = isPlainObject(inner) && typeof own(inner, "code") === "string" ? String(own(inner, "code")).toLowerCase() : "";
  if (code && Object.hasOwn(SETTINGS_ERRORS, code)) return SETTINGS_ERRORS[code];
  if (status === 403) return SETTINGS_ERRORS.not_permitted;
  if (status === 409) return "Something changed at the same time. Reload the page and try again.";
  if (status === 422) return "Check what you typed: a reason is required, as plain text.";
  return "This could not be saved. Check your connection and try again.";
}
