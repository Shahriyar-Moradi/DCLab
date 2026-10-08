/**
 * Inbox view models (P4.16-UI). Pure: no React, no fetch. The API decides who may act; `can_act` / `allowed` only
 * shape the UI. Every id is UUID-checked before it becomes a link and every link goes through `safeInternalHref`.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid } from "./command-search.ts";
import { envelope, mapWizardError, type PlainError } from "./studio-wizard.ts";

/** Lookup that never reads inherited members: a key like `constructor` or `__proto__` falls back. */
export function lookup<T>(table: Record<string, T>, key: string | null | undefined, fallback: T): T {
  return typeof key === "string" && Object.hasOwn(table, key) ? table[key] : fallback;
}

export type InboxTabId = "needs_decision" | "applied_automatically" | "done";

export const INBOX_TABS: Array<{ id: InboxTabId; label: string; empty: string }> = [
  { id: "needs_decision", label: "Needs a decision", empty: "Nothing is waiting for you. Suggestions from the assistant and from people, and runs that ask a question, show up here." },
  {
    id: "applied_automatically", label: "Applied automatically",
    empty: "Nothing was applied without a person. Every kind of decision is set to Ask first today, so every AI answer is shown to you first. When one is set to Automatic, you can undo, what the assistant applied on its own is listed here with an Undo.",
  },
  { id: "done", label: "Done", empty: "Nothing has been decided or finished yet." },
];

export type InboxAction = { name: string; operation: string; path_params: Record<string, string>; body: Record<string, string>; allowed: boolean };
export type InboxItemLike = {
  id: string; kind: string; project_id?: string | null; status: string; level?: number | null;
  subject: { kind: string; id?: string | null };
  evidence_refs: Array<{ kind: string; id: string }>;
  actions: InboxAction[]; can_act: boolean;
};

export function findAction(item: Pick<InboxItemLike, "actions">, name: string): InboxAction | undefined {
  return item.actions.find((a) => a.name === name);
}

export type ActionRoute =
  | "decision_accept" | "decision_reject" | "decision_supersede" | "ref_move_accept"
  | "proposal_accept" | "proposal_reject" | "proposal_revert";

/** The only (kind, action) pairs the inbox sends, each tied to the literal route the server must name. */
const ROUTES: Record<string, Array<[string, ActionRoute]>> = {
  "decision_proposal|accept": [["POST /v1/decisions/{decision_id}/accept", "decision_accept"], ["POST /v1/projects/{project_id}/refs/{ref_kind}", "ref_move_accept"]],
  "decision_proposal|reject": [["POST /v1/decisions/{decision_id}/reject", "decision_reject"]],
  "decision_proposal|supersede": [["POST /v1/decisions/{decision_id}/supersede", "decision_supersede"]],
  "agent_proposal|accept": [["POST /v1/proposals/{proposal_id}/accept", "proposal_accept"]],
  "agent_proposal|reject": [["POST /v1/proposals/{proposal_id}/reject", "proposal_reject"]],
  "agent_proposal|revert": [["POST /v1/proposals/{proposal_id}/revert", "proposal_revert"]],
};

/** The route to call for an action, or null when the kind, name or the server's `operation` is not on the allow-list. */
export function actionRoute(item: Pick<InboxItemLike, "kind" | "actions">, name: string): ActionRoute | null {
  const action = findAction(item, name);
  if (!action || !Object.hasOwn(ROUTES, `${item.kind}|${name}`)) return null;
  return ROUTES[`${item.kind}|${name}`].find(([operation]) => operation === action.operation)?.[1] ?? null;
}

/** Ref versions for `ref_versions`: every ref kind of the project, `null` for a kind with no ref yet. */
const REF_KINDS = ["problem_spec", "dataset", "split_plan", "feature_recipe", "champion_model"] as const;
export function refVersionsFor(refs: Array<{ ref_kind: string; version: number }>): Record<string, number | null> {
  return Object.fromEntries(REF_KINDS.map((kind) => [kind, refs.find((r) => r.ref_kind === kind)?.version ?? null]));
}

/** Why the buttons are off. The API enforces the role; this only explains it. */
export function disabledReason(item: Pick<InboxItemLike, "actions" | "kind">, name: string, viewerCanDecide: boolean): string | null {
  const action = findAction(item, name);
  if (!action) return null;
  if (actionRoute(item, name) === null) return "This action is not available from the inbox. Open the item's project to decide it there.";
  if (action.allowed) return null;
  if (!viewerCanDecide) return "Your role can read the inbox but not decide. Ask a workspace member who can write ML work.";
  if (item.kind === "agent_proposal" && name === "accept") return "Only the person whose conversation made this assistant proposal can accept it. You can still reject it.";
  return "You cannot take this action here.";
}

export type EvidenceLink = { key: string; label: string; href: string | null };

const KIND_LABEL: Record<string, string> = {
  experiment: "Run", decision_record: "History entry", agent_run: "Assistant run", semantic_answer: "Column review answer",
};

function evidenceHref(kind: string, id: string, projectId: string | null | undefined): string | null {
  if (!isUuid(projectId) || !isUuid(id)) return null;
  if (kind === "experiment") return safeInternalHref(`/projects/${projectId}/experiments/${id}`);
  if (kind === "decision_record") return safeInternalHref(`/projects/${projectId}/decisions?record=${id}`);
  return null;
}

/** Evidence refs as labelled links; kinds without a page are shown as plain text with a short id. */
export function evidenceLinks(item: Pick<InboxItemLike, "evidence_refs" | "project_id">): EvidenceLink[] {
  return item.evidence_refs.map((ref) => ({
    key: `${ref.kind}:${ref.id}`,
    label: `${lookup(KIND_LABEL, ref.kind, ref.kind.replaceAll("_", " "))} ${ref.id.slice(0, 8)}`,
    href: evidenceHref(ref.kind, ref.id, item.project_id),
  }));
}

/** The item's subject as a link when its page exists (a run or a decision record). */
export function subjectLink(item: Pick<InboxItemLike, "subject" | "project_id">): EvidenceLink | null {
  const { kind, id } = item.subject;
  if (!id) return null;
  return { key: `${kind}:${id}`, label: `${lookup(KIND_LABEL, kind, kind.replaceAll("_", " "))} ${id.slice(0, 8)}`, href: evidenceHref(kind, id, item.project_id) };
}

/** Runs the card can compare: every distinct run named by the subject or evidence (two or more needed). */
export function comparableRunIds(item: Pick<InboxItemLike, "subject" | "evidence_refs">): string[] {
  const ids = [item.subject.kind === "experiment" ? item.subject.id : null, ...item.evidence_refs.filter((r) => r.kind === "experiment").map((r) => r.id)];
  return [...new Set(ids.filter((id): id is string => isUuid(id)))];
}

export function inboxCompareHref(projectId: string | null | undefined, ids: string[]): string | null {
  if (!isUuid(projectId) || ids.length < 2 || !ids.every(isUuid)) return null;
  return safeInternalHref(`/projects/${projectId}/experiments/compare?ids=${ids.join(",")}`);
}

/** Where a question is answered: the run page (the confirmation forms live there). */
export function questionHref(item: Pick<InboxItemLike, "subject" | "project_id">): string | null {
  const { kind, id } = item.subject;
  if (kind === "experiment" && isUuid(id) && isUuid(item.project_id)) return safeInternalHref(`/projects/${item.project_id}/experiments/${id}`);
  return isUuid(item.project_id) ? safeInternalHref(`/projects/${item.project_id}/experiments`) : null;
}

/** Inbox write failures in plain language (the API's `owner_only` is shown as such). */
export function mapInboxError(error: unknown): PlainError {
  const status = (error as { status?: unknown } | null)?.status;
  const { code, message } = envelope((error as { body?: unknown } | null)?.body);
  if (code === "owner_only") return { title: "Only the owner can do this", detail: "Only the person whose conversation this came from can do this. An approver can still reject it.", fixable: false };
  if (status === 428 || code === "ref_versions_required") return { title: "The page is out of date", detail: "The versions currently in use were not available. Reload the inbox and try again.", fixable: false };
  if (status === 412 || code === "ref_version_conflict" || code === "precondition_failed") return { title: "This changed while you were looking", detail: "Reload the inbox to see the current state, then decide again.", fixable: false };
  if (status === 403) return { title: "You cannot do this here", detail: "Deciding needs a role that can write ML work in this workspace. The API enforces this; the disabled button is only a hint.", fixable: false };
  if (status === 404) return { title: "This item is gone", detail: "It is not in this workspace any more, or you cannot see it. Reload the inbox.", fixable: false };
  if (status === 409 || code === "invalid_decision_transition") return { title: "Already decided", detail: message || "Someone else resolved this first, or it expired. Reload the inbox.", fixable: false };
  return mapWizardError(error);
}

export const INBOX_BADGE_REFETCH_MS = 60_000;

export function badgeText(count: number | undefined): string | null {
  if (!count || count < 1) return null;
  return count > 99 ? "99+" : String(count);
}

export const PROPOSED_BY_LABEL: Record<string, string> = {
  agent: "connected tool (access token)", assistant: "the assistant", jev: "AI reviewer", rule: "suggested by the rules", person: "person", run: "run",
};

const ANSWER_LINES_MAX = 8;
const ANSWER_VALUE_MAX = 160;
// Controls, DEL, C1 controls and the Unicode line separators (built from escapes, never literal).
const CONTROL_CHARS = new RegExp("[\\u0000-\\u001f\\u007f-\\u009f\\u2028\\u2029]", "g");

function plain(value: unknown): string {
  return String(value).replace(CONTROL_CHARS, " ").slice(0, ANSWER_VALUE_MAX);
}

/**
 * A recorded rule / AI answer as plain `key: value` lines (no raw JSON, no markup). Nested values are summarised;
 * values are untrusted text and are only ever rendered as text.
 */
export function answerLines(answer: Record<string, unknown> | null | undefined): Array<{ key: string; value: string }> {
  if (!answer) return [];
  return Object.entries(answer).slice(0, ANSWER_LINES_MAX).map(([key, value]) => {
    let text: string;
    if (value === null || value === undefined) text = "none";
    else if (Array.isArray(value)) text = value.every((v) => typeof v !== "object" || v === null) ? value.slice(0, 5).map(plain).join(", ") + (value.length > 5 ? ", …" : "") : `${value.length} entries`;
    else if (typeof value === "object") text = `${Object.keys(value).length} fields`;
    else text = plain(value);
    return { key: plain(key).replaceAll("_", " "), value: text || "empty" };
  });
}
