/**
 * Assistant panel view models (D6b, labelled Beta). Pure: no React, no fetch, so `npm run test:components` covers them.
 *
 * Everything the model or a person wrote is untrusted plain text: it is only ever rendered as React text (never
 * innerHTML, never Markdown). The one structure read out of a reply is the `[label](dclab://<kind>/<id>)` reference the
 * API allows, mapped here to an internal route built from UUIDs and checked by `safeInternalHref`; any other link-like
 * text stays literal text. Errors and refusals are shown as fixed sentences chosen by code, never the server's message.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid, plainText } from "./command-search.ts";
import { aiIsOff } from "./studio-home.ts";
import { lookup } from "./studio-inbox.ts";

export const BETA_LABEL = "Beta";
export const BETA_NOTE =
  "Beta: the AI model and its budget are not confirmed yet. The assistant reads this project and suggests; a person accepts every change. Every action is also a button on its screen.";

/* ---------- gating: shown only when AI is available AND the workspace has the beta ---------- */

type GateGovernance = {
  ai_enabled_setting: boolean; policy_unavailable?: string | null; assistant_beta?: boolean;
  switches: { platform_ai_blocking: string | null; workspace: Array<{ switch_key: string; state: string; held_by_incident: boolean }> };
};

type SwitchLike = { switch_key: string; state: string; held_by_incident: boolean };
const isOff = (s: SwitchLike) => s.state === "off" || s.held_by_incident;

/** True when an owner or admin (or a problem report) holds AI off for the whole workspace. */
export function workspaceAiHeldOff(g: Pick<GateGovernance, "switches">): boolean {
  return g.switches.workspace.some((s) => s.switch_key === "all_ai" && isOff(s));
}

/** Workspace switches other than all_ai that are off and stop the assistant: its helper, its use, or its provider. */
export function assistantSwitchesOff(g: Pick<GateGovernance, "switches">, provider?: string | null): string[] {
  return g.switches.workspace
    .filter((s) => isOff(s) && (s.switch_key === "agent:lead" || s.switch_key.startsWith("purpose:assistant") || (!!provider && s.switch_key === `provider:${provider}`)))
    .map((s) => s.switch_key);
}

/** "available" only when the read says the beta is on and nothing says AI is off; unknown (loading, refused) is hidden. */
export function assistantGate(g: (GateGovernance & { model_in_use?: { provider?: string | null } | null }) | null | undefined): "available" | "hidden" {
  if (!g || g.assistant_beta !== true) return "hidden";
  return aiIsOff(g) || workspaceAiHeldOff(g) || assistantSwitchesOff(g, g.model_in_use?.provider).length > 0 ? "hidden" : "available";
}

// Bidirectional-override controls (U+202A–202E, U+2066–2069) can reorder what a reader sees; they are dropped from shown text.
const BIDI = new RegExp("[\\u202a-\\u202e\\u2066-\\u2069]", "g");
export function stripBidi(text: string): string {
  return text.replace(BIDI, "");
}

/* ---------- Server-Sent Events: an incremental, bounded parser ---------- */

export const ASSISTANT_EVENT_TYPES = [
  "user_message", "turn_started", "tool_call", "tool_result", "step_rejected", "call_refused", "proposal_created",
  "assistant_message", "budget_exhausted", "turn_done",
] as const;
export type AssistantEventType = (typeof ASSISTANT_EVENT_TYPES)[number];
export type AssistantEvent = { type: AssistantEventType; turn_id: string; seq: number; data: Record<string, unknown> };

const KNOWN_TYPES: ReadonlySet<string> = new Set(ASSISTANT_EVENT_TYPES);
/** One event's data (and the pending line buffer) may not grow past this; a larger one is dropped as malformed. */
export const SSE_MAX_CHARS = 256_000;

/** A JSON object with its own keys only (arrays, primitives and null are not objects here). */
export function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** An own field of untrusted data, never an inherited one (`__proto__`, `constructor`). */
export function own(data: Record<string, unknown>, key: string): unknown {
  return Object.hasOwn(data, key) ? data[key] : undefined;
}

/**
 * Feed decoded text chunks as they arrive; complete events come back in order. Lines may end in LF, CRLF or CR and may be
 * split anywhere across chunks. Comments and unknown fields are ignored; an unknown event type, data that is not a JSON
 * object, or an oversized event is skipped and counted in `malformed`. `turn_id` is read from the data (the API puts it there).
 */
export class SseParser {
  private buffer = "";
  private eventType = "";
  private data: string[] = [];
  private dataChars = 0;
  private lastId = "";
  private overflow = false;
  malformed = 0;

  feed(chunk: string): AssistantEvent[] {
    this.buffer += chunk;
    const out: AssistantEvent[] = [];
    for (;;) {
      const match = /\r\n|\r|\n/.exec(this.buffer);
      // A trailing CR may be the first half of CRLF: wait for the next chunk (end() flushes it).
      if (!match || (match[0] === "\r" && match.index === this.buffer.length - 1)) break;
      const line = this.buffer.slice(0, match.index);
      this.buffer = this.buffer.slice(match.index + match[0].length);
      this.line(line, out);
    }
    if (this.buffer.length > SSE_MAX_CHARS) {
      this.buffer = "";
      this.overflow = true;
    }
    return out;
  }

  /** The stream ended: a final unterminated line is processed and a pending event dispatched. */
  end(): AssistantEvent[] {
    const out: AssistantEvent[] = [];
    if (this.buffer) this.line(this.buffer.replace(/\r$/, ""), out);
    this.buffer = "";
    this.dispatch(out);
    return out;
  }

  private line(line: string, out: AssistantEvent[]): void {
    if (line === "") return this.dispatch(out);
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const field = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") this.eventType = value;
    else if (field === "id" && !value.includes("\u0000")) this.lastId = value;
    else if (field === "data") {
      this.dataChars += value.length + 1;
      if (this.dataChars > SSE_MAX_CHARS) this.overflow = true;
      else this.data.push(value);
    }
  }

  private dispatch(out: AssistantEvent[]): void {
    const type = this.eventType || "message";
    const raw = this.data.join("\n");
    const hadData = this.data.length > 0 || this.overflow;
    const overflow = this.overflow;
    this.eventType = "";
    this.data = [];
    this.dataChars = 0;
    this.overflow = false;
    if (!hadData) return;
    if (overflow || !KNOWN_TYPES.has(type)) {
      this.malformed += 1;
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(raw);
    } catch {
      this.malformed += 1;
      return;
    }
    if (!isPlainObject(parsed)) {
      this.malformed += 1;
      return;
    }
    const turn = own(parsed, "turn_id");
    const data: Record<string, unknown> = {};
    for (const key of Object.keys(parsed)) if (key !== "turn_id" && key !== "__proto__") data[key] = parsed[key];
    const seq = /^\d{1,9}$/.test(this.lastId) ? Number(this.lastId) : -1;
    out.push({ type: type as AssistantEventType, turn_id: typeof turn === "string" ? turn : "", seq, data });
  }
}

/** Events of the thread read, filtered to the known types (anything else is dropped, never guessed). */
export function knownEvents(events: Array<{ type: string; turn_id: string; seq: number; data: Record<string, unknown> }>): AssistantEvent[] {
  return events.filter((e) => KNOWN_TYPES.has(e.type) && isPlainObject(e.data)) as AssistantEvent[];
}

/* ---------- turns: events folded into what the panel shows ---------- */

export type TurnView = {
  turnId: string;
  userText: string | null;
  replies: Array<{ text: string; templated: boolean }>;
  /** Steps the assistant ran, in plain words. */
  steps: string[];
  /** Refusals, failed checks and limits, as fixed sentences. */
  notes: string[];
  proposalIds: string[];
  done: boolean;
  failed: string | null;
};

/** A turn shown while it streams; history turns never say "Writing…". */
export const EMPTY_TURN: TurnView = { turnId: "live", userText: null, replies: [], steps: [], notes: [], proposalIds: [], done: false, failed: null };

const MESSAGE_MAX = 8000;

const TOOL_WORDS: Record<string, string> = {
  get_project: "read the project", list_experiments: "listed the runs", get_experiment: "read a run", get_dataset: "read a data version",
  inspect_dataset: "read the column summaries", get_findings: "read the trust checks", get_model: "read a model", get_monitoring: "read monitoring",
  get_problem_spec: "read the goal", get_split_plan: "read the test design", list_decisions: "read the history", search: "searched this project",
  create_problem_spec: "drafted a goal", propose_problem_spec: "drafted a goal", record_decision: "drafted a history entry",
  request_agent_review: "asked for a review", move_ref: "drafted a change of what the project uses", run_experiment: "drafted a run",
  branch_experiment: "drafted a variation of a run", predict: "drafted a scoring job", improve: "drafted an improvement loop",
};

/** A tool name in plain words; an unknown name keeps its cleaned words (never markup). */
export function toolWords(tool: unknown): string {
  if (typeof tool !== "string" || !tool) return "ran a step";
  return lookup(TOOL_WORDS, tool, plainText(tool.replaceAll("_", " "), 40) || "ran a step");
}

const REFUSAL_WORDS: Record<string, string> = {
  budget_exhausted: "A step was stopped because a spending limit was reached.",
  timeout: "A step took too long and was stopped.",
  capability_denied: "A step was refused: the assistant may not use that here.",
  policy_denied: "A step was refused by this workspace's AI settings.",
  rate_limited: "A step was refused: too many requests.",
};
const BUDGET_WORDS: Record<string, string> = {
  turn: "This answer reached its spending limit.",
  thread: "This conversation reached its spending limit. Start a new conversation to go on.",
  user: "You reached your limit for assistant answers. Try again later.",
  workspace: "This workspace reached its monthly AI budget.",
};
const DONE_WORDS: Record<string, string> = {
  turn_stream_ended: "The live view ended early. The answer is saved: reopen the conversation to see it.",
  turn_not_started: "This answer could not start. Try again.",
};

function addUnique(list: string[], text: string): void {
  if (!list.includes(text)) list.push(text);
}

/**
 * Events in arrival order, grouped by turn (first seen first). Only own fields are read; every text is capped plain text.
 * A templated (AI off) turn has no `turn_done` and counts as done; a proposal id is listed once across all turns.
 */
export function foldTurns(events: AssistantEvent[]): TurnView[] {
  const turns = new Map<string, TurnView>();
  const seenProposals = new Set<string>();
  const calls = new Map<string, Map<string, { words: string; ok: boolean | null }>>();
  const addProposal = (turn: TurnView, id: unknown) => {
    if (isUuid(id) && !seenProposals.has(id)) {
      seenProposals.add(id);
      turn.proposalIds.push(id);
    }
  };
  const turnOf = (id: string): TurnView => {
    const key = id || "unknown";
    let turn = turns.get(key);
    if (!turn) {
      turn = { turnId: key, userText: null, replies: [], steps: [], notes: [], proposalIds: [], done: false, failed: null };
      turns.set(key, turn);
    }
    return turn;
  };
  for (const event of events) {
    const turn = turnOf(event.turn_id);
    const d = event.data;
    switch (event.type) {
      case "user_message": {
        const text = own(d, "message");
        if (typeof text === "string") turn.userText = stripBidi(text.slice(0, MESSAGE_MAX));
        break;
      }
      case "tool_call":
      case "tool_result": {
        const byCall = calls.get(turn.turnId) ?? new Map<string, { words: string; ok: boolean | null }>();
        calls.set(turn.turnId, byCall);
        const key = String(own(d, "call") ?? byCall.size);
        const known = byCall.get(key);
        const ok = own(d, "ok");
        byCall.set(key, { words: known?.words ?? toolWords(own(d, "tool")), ok: event.type === "tool_result" && typeof ok === "boolean" ? ok : known?.ok ?? null });
        break;
      }
      case "step_rejected":
        addUnique(turn.notes, "A draft step failed a safety check and was not used.");
        break;
      case "call_refused": {
        const code = own(d, "code");
        addUnique(turn.notes, typeof code === "string" ? lookup(REFUSAL_WORDS, code, "A step was refused by the rules.") : "A step was refused by the rules.");
        break;
      }
      case "budget_exhausted": {
        const scope = own(d, "scope");
        addUnique(turn.notes, typeof scope === "string" ? lookup(BUDGET_WORDS, scope, "A spending limit was reached.") : "A spending limit was reached.");
        break;
      }
      case "proposal_created":
        addProposal(turn, own(d, "proposal_id"));
        break;
      case "assistant_message": {
        const text = own(d, "message");
        if (typeof text === "string" && text.trim()) turn.replies.push({ text: stripBidi(text.slice(0, MESSAGE_MAX)), templated: own(d, "templated") === true });
        break;
      }
      case "turn_done": {
        turn.done = true;
        const status = own(d, "status");
        const code = own(d, "error_code");
        if (status === "failed" || (typeof code === "string" && code)) {
          turn.failed = typeof code === "string" ? lookup(DONE_WORDS, code, "This answer could not finish. Use the forms or the quick actions instead.") : "This answer could not finish. Use the forms or the quick actions instead.";
        }
        for (const id of Array.isArray(own(d, "proposal_ids")) ? (own(d, "proposal_ids") as unknown[]) : []) addProposal(turn, id);
        break;
      }
      default:
        break;
    }
  }
  for (const turn of turns.values()) {
    for (const step of calls.get(turn.turnId)?.values() ?? []) addUnique(turn.steps, step.ok === false ? `${step.words} (did not work)` : step.words);
    if (!turn.done && turn.replies.length > 0 && turn.replies.every((r) => r.templated)) turn.done = true;
    // A thread-level limit note (no user text, no reply) is a record, not an answer being written.
    if (!turn.done && turn.userText === null && turn.replies.length === 0 && turn.notes.length > 0) turn.done = true;
  }
  return [...turns.values()];
}

/* ---------- reply text: dclab:// references to internal routes, everything else literal ---------- */

export type ReplySegment = { kind: "text"; text: string } | { kind: "link"; label: string; href: string | null };

const NODE_LINK = /\[([^[\]\n]{1,200})\]\(dclab:\/\/([a-z_]{1,32})\/([0-9a-f-]{36})\)/g;

/** Where a cited node opens inside its project; kinds without a page are shown as their label only. */
export function dclabHref(kind: string, id: string, projectId: string | null | undefined): string | null {
  if (!isUuid(projectId) || !isUuid(id)) return null;
  const base = `/projects/${projectId}`;
  const routes: Record<string, string> = {
    experiment: `${base}/experiments/${id}`, dataset_version: `${base}/data/${id}`, model_version: `${base}/models/${id}`,
    split_plan: `${base}/splits/${id}`, problem_spec: `${base}/goal`, decision: `${base}/decisions?record=${id}`, proposal: "/inbox",
  };
  return Object.hasOwn(routes, kind) ? safeInternalHref(routes[kind]) : null;
}

/** A reply as text and link segments. Only the exact `[label](dclab://kind/uuid)` form becomes a link. */
export function replySegments(message: string, projectId: string | null | undefined): ReplySegment[] {
  const text = String(message ?? "").slice(0, MESSAGE_MAX);
  const out: ReplySegment[] = [];
  let at = 0;
  for (const match of text.matchAll(NODE_LINK)) {
    const index = match.index ?? 0;
    if (index > at) out.push({ kind: "text", text: text.slice(at, index) });
    out.push({ kind: "link", label: plainText(match[1], 200) || "link", href: dclabHref(match[2], match[3], projectId) });
    at = index + match[0].length;
  }
  if (at < text.length) out.push({ kind: "text", text: text.slice(at) });
  return out;
}

/* ---------- page context: the screen and the ids in the route, nothing else ---------- */

export type PageContext = {
  screen: "home" | "project" | "data" | "goal" | "experiments" | "experiment" | "improve" | "models" | "model" | "predictions" | "monitoring" | "decisions" | "graph" | "inbox";
  experiment_id?: string; dataset_id?: string; model_version_id?: string; split_plan_id?: string;
};

const SECTION_SCREEN: Record<string, PageContext["screen"]> = {
  data: "data", goal: "goal", splits: "goal", experiments: "experiments", pipeline: "experiments", features: "experiments", improve: "improve",
  models: "models", predictions: "predictions", monitoring: "monitoring", decisions: "decisions", graph: "graph",
};

/** The project id and page context of a Studio path; ids come from the route only and must be UUIDs. */
export function pageContextFor(pathname: string): { projectId: string | null; context: PageContext | null } {
  const parts = pathname.split(/[?#]/)[0].split("/").filter(Boolean);
  if (parts[0] === "home") return { projectId: null, context: { screen: "home" } };
  if (parts[0] === "inbox") return { projectId: null, context: { screen: "inbox" } };
  if (parts[0] !== "projects" || !isUuid(parts[1])) return { projectId: null, context: null };
  const projectId = parts[1];
  const section = parts[2];
  const child = isUuid(parts[3]) ? parts[3] : null;
  if (!section) return { projectId, context: { screen: "project" } };
  if (!Object.hasOwn(SECTION_SCREEN, section)) return { projectId, context: { screen: "project" } };
  const screen = SECTION_SCREEN[section];
  if (child && section === "experiments") return { projectId, context: { screen: "experiment", experiment_id: child } };
  if (child && section === "models") return { projectId, context: { screen: "model", model_version_id: child } };
  if (child && section === "data") return { projectId, context: { screen: "data", dataset_id: child } };
  if (child && section === "splits") return { projectId, context: { screen: "goal", split_plan_id: child } };
  return { projectId, context: { screen } };
}

/* ---------- cost wording ---------- */

/** Dollars from micro-dollars: "$0.19", "less than $0.01", "$0.00". */
export function money(micros: number | null | undefined): string {
  if (typeof micros !== "number" || !Number.isFinite(micros) || micros <= 0) return "$0.00";
  const dollars = micros / 1_000_000;
  return dollars < 0.01 ? "less than $0.01" : `$${dollars.toFixed(2)}`;
}

/** Why a cost is not exact: an unconfirmed price is an upper bound; otherwise it is DCLab's own estimate. */
export function costQualifier(upperBound: boolean): string {
  return upperBound ? "upper bound: the model's price is not confirmed yet" : "estimate from DCLab's own records";
}

export function conversationCostText(micros: number, upperBound: boolean, limitMicros?: unknown): string {
  const limit = typeof limitMicros === "number" && limitMicros > 0 ? ` of ${money(limitMicros)}` : "";
  return `Cost of this conversation: ${money(micros)}${limit} (${costQualifier(upperBound)})`;
}

export function turnCostText(micros: number | null | undefined, templated: boolean, upperBound: boolean): string {
  if (templated) return "No AI used";
  if (typeof micros !== "number") return "Cost not recorded";
  return `${money(micros)} (${upperBound ? "upper bound" : "estimate"})`;
}

/* ---------- errors: fixed sentences by status and code ---------- */

export type AssistantError = { title: string; detail: string };

const BY_CODE: Record<string, AssistantError> = {
  assistant_beta_off: { title: "The assistant beta is not on for this workspace", detail: "DCLab switches the beta on for chosen workspaces. Everything works without it: every action is also a button." },
  human_session_required: { title: "The assistant is for signed-in people", detail: "Connected tools use their own access, not this panel." },
  turn_in_progress: { title: "An answer is still being written", detail: "Wait for it to finish, then send your message again." },
  run_already_active: { title: "An answer is still being written", detail: "Wait for it to finish, then send your message again." },
  idempotency_key_conflict: { title: "That message was already sent", detail: "It was sent with different text. Send it again." },
  page_context_invalid: { title: "This screen could not be shared with the assistant", detail: "Open a page of this project and send the message again." },
  thread_limit_reached: { title: "This conversation reached its limit", detail: "Start a new conversation to go on." },
  user_turn_limit: { title: "You reached the hourly limit", detail: "Try again later. Every action is also a button on its screen." },
  user_turn_limit_concurrent: { title: "Another answer of yours is still being written", detail: "Wait for it to finish, then send again." },
  too_many_streams: { title: "This answer is already open elsewhere", detail: "It is being shown in another tab. Reopen the conversation when it ends." },
  assistant_unavailable: { title: "The assistant is not ready in this workspace", detail: "Use the forms or the quick actions for now." },
  policy_unavailable: { title: "The AI settings could not be read", detail: "AI is refused until they can. Every action is also a button." },
  assistant_busy: { title: "Too many answers are being written", detail: "Try again in a minute." },
};
const BY_STATUS: Record<string, AssistantError> = {
  "403": { title: "You cannot use the assistant here", detail: "Your role in this workspace does not allow it." },
  "404": { title: "This conversation is not available", detail: "It belongs to someone else or is no longer there." },
  "409": { title: "Something changed at the same time", detail: "Try again." },
  "422": { title: "The message was not accepted", detail: "Keep it under 4,000 characters, as plain text." },
  "429": { title: "Too many requests", detail: "Try again shortly." },
  "503": { title: "The assistant is unavailable right now", detail: "Every action is also a button on its screen." },
};
const FALLBACK: AssistantError = { title: "The assistant could not answer", detail: "Check your connection and try again." };

/** An assistant failure as a fixed sentence (the server's message text is never shown). */
export function assistantError(error: unknown): AssistantError {
  const status = (error as { status?: unknown } | null)?.status;
  const body = (error as { body?: unknown } | null)?.body;
  const inner = isPlainObject(body) ? own(body, "error") : undefined;
  const code = isPlainObject(inner) && typeof own(inner, "code") === "string" ? String(own(inner, "code")).toLowerCase() : "";
  const details = isPlainObject(inner) ? own(inner, "details") : undefined;
  if (code === "user_turn_limit" && isPlainObject(details) && own(details, "limit") === "concurrent_turns") return BY_CODE.user_turn_limit_concurrent;
  if (code && code !== "user_turn_limit_concurrent" && Object.hasOwn(BY_CODE, code)) return BY_CODE[code];
  return typeof status === "number" ? lookup(BY_STATUS, String(status), FALLBACK) : FALLBACK;
}

/** The stream closed before `turn_done`: the turn goes on server-side. */
export const STREAM_ENDED: AssistantError = { title: "The live view ended early", detail: "It is still being written or saved; reopen the conversation in a moment." };

const PROPOSAL_BY_CODE: Record<string, AssistantError> = {
  owner_only: { title: "Only the person who had this conversation can accept it", detail: "An approver can still reject it." },
  proposal_not_open: { title: "Already decided", detail: "This suggestion is no longer waiting for a decision." },
  proposal_superseded: { title: "Replaced by a newer suggestion", detail: "It cannot be accepted any more. See the Inbox." },
  proposal_expired: { title: "This suggestion expired", detail: "Ask the assistant again if you still want it." },
  proposal_not_applied: { title: "Nothing to undo", detail: "This suggestion was not applied." },
  not_revertible_in_place: { title: "This cannot be undone here", detail: "Make the change again from its screen." },
  ref_versions_required: { title: "The page is out of date", detail: "Reopen the conversation and try again." },
  ref_version_conflict: { title: "This changed while you were looking", detail: "Reopen the conversation to see the current state, then decide again." },
  precondition_failed: { title: "This changed while you were looking", detail: "Reopen the conversation to see the current state, then decide again." },
  human_session_required: { title: "Only a signed-in person can decide", detail: "Connected tools never accept or reject suggestions." },
};
const PROPOSAL_BY_STATUS: Record<string, AssistantError> = {
  "403": { title: "You cannot decide this here", detail: "Deciding needs a role that can write ML work in this workspace." },
  "404": { title: "This suggestion is gone", detail: "It is not in this workspace any more, or you cannot see it." },
  "409": { title: "Already decided or no longer possible", detail: "Open the Inbox to see its current state." },
  "412": PROPOSAL_BY_CODE.ref_version_conflict,
  "422": { title: "This suggestion could not be applied", detail: "Its values are no longer valid for this project. See the Inbox." },
  "428": PROPOSAL_BY_CODE.ref_versions_required,
  "429": { title: "Too many runs at once", detail: "Wait for a run to finish, then try again." },
};

/** Accept / reject failures of a suggestion card, as fixed sentences (the server's message is never shown). */
export function proposalError(error: unknown): AssistantError {
  const status = (error as { status?: unknown } | null)?.status;
  const body = (error as { body?: unknown } | null)?.body;
  const inner = isPlainObject(body) ? own(body, "error") : undefined;
  const code = isPlainObject(inner) && typeof own(inner, "code") === "string" ? String(own(inner, "code")).toLowerCase() : "";
  if (code && Object.hasOwn(PROPOSAL_BY_CODE, code)) return PROPOSAL_BY_CODE[code];
  const fallback = { title: "This could not be saved", detail: "Check your connection and try again. The Inbox lists it too." };
  return typeof status === "number" ? lookup(PROPOSAL_BY_STATUS, String(status), fallback) : fallback;
}

/** True for the beta-off refusal: the panel then closes instead of offering a retry. */
export function isBetaOff(error: unknown): boolean {
  return assistantError(error) === BY_CODE.assistant_beta_off;
}

/* ---------- per-user preferences in the browser (never required to work) ---------- */

type StorageLike = { getItem(key: string): string | null; setItem(key: string, value: string): void };

export function readFlag(storage: () => StorageLike | null | undefined, key: string): boolean {
  try {
    return storage()?.getItem(key) === "on";
  } catch {
    return false;
  }
}

export function writeFlag(storage: () => StorageLike | null | undefined, key: string, on: boolean): void {
  try {
    storage()?.setItem(key, on ? "on" : "off");
  } catch {
    // Private windows and blocked storage: the choice lasts for this page only.
  }
}

export const SUGGESTIONS_FLAG = "dclab.ai-suggestions.v1";

/* ---------- per-screen "AI suggestions": existing open proposals about what the screen shows ---------- */

type ProposalLike = { id: string; project_id: string; open: boolean; subject: { kind: string; id?: string | null } };

/** Subject kinds a list screen is about (a screen about one node matches that node only). */
const SCREEN_SUBJECTS: Record<string, string[]> = {
  project: ["project"], data: ["dataset_version"], goal: ["problem_spec", "split_plan"], experiments: ["experiment"], improve: ["experiment"],
  models: ["model_version"], predictions: ["model_version"], monitoring: ["monitoring_window", "model_version"],
};

/**
 * Open proposals of this project about what the current screen shows. Read-only: the panel lists them and links to the
 * Inbox, where a person decides; nothing here ever accepts or rejects.
 */
export function screenSuggestions<T extends ProposalLike>(proposals: T[], pathname: string): T[] {
  const { projectId, context } = pageContextFor(pathname);
  if (!projectId || !context) return [];
  const one = context.experiment_id ?? context.dataset_id ?? context.model_version_id ?? context.split_plan_id ?? null;
  const kinds = one ? null : lookup(SCREEN_SUBJECTS, context.screen, [] as string[]);
  return proposals.filter((p) => p.open && p.project_id === projectId && (one ? p.subject.id === one : kinds!.includes(p.subject.kind))).slice(0, 5);
}
