/**
 * Inbox view models (P4.16-UI). Pure: no React, no fetch. The API decides who may act; `can_act` / `allowed` only
 * shape the UI. Every id is UUID-checked before it becomes a link and every link goes through `safeInternalHref`.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid, plainText } from "./command-search.ts";
import { statusWords } from "./studio-runs.ts";
import { envelope, mapWizardError, type PlainError } from "./studio-wizard.ts";

/** Lookup that never reads inherited members: a key like `constructor` or `__proto__` falls back. */
export function lookup<T>(table: Record<string, T>, key: string | null | undefined, fallback: T): T {
  return typeof key === "string" && Object.hasOwn(table, key) ? table[key] : fallback;
}

export type InboxTabId = "needs_decision" | "applied_automatically" | "done";

export const INBOX_TABS: Array<{ id: InboxTabId; label: string; empty: string }> = [
  { id: "needs_decision", label: "Needs your decision", empty: "Nothing is waiting for you. Suggestions from the rules, the assistant, connected tools and people, and runs that ask you a question, show up here." },
  {
    id: "applied_automatically", label: "Applied automatically",
    empty: "Nothing listed here. Items applied on their own appear here; some can be undone.",
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
  if (actionRoute(item, name) === null) return "This action is not available from the inbox. Open the item's project and decide it from its History.";
  if (action.allowed) return null;
  if (!viewerCanDecide) return "Your role can read the inbox but not decide. Ask a workspace member who can write ML work.";
  if (item.kind === "agent_proposal" && name === "accept") return "Only the person whose conversation made this assistant suggestion can approve it. You can still reject it.";
  return "You cannot take this action here.";
}

export type EvidenceLink = { key: string; label: string; href: string | null };

const KIND_LABEL: Record<string, string> = {
  experiment: "Run", decision_record: "History entry", agent_run: "AI agent run", semantic_answer: "Column review answer", project: "Project",
};

function evidenceHref(kind: string, id: string, projectId: string | null | undefined): string | null {
  if (!isUuid(projectId) || !isUuid(id)) return null;
  if (kind === "experiment") return safeInternalHref(`/projects/${projectId}/experiments/${id}`);
  if (kind === "decision_record") return safeInternalHref(`/projects/${projectId}/decisions?record=${id}`);
  return null;
}

/** A name for an evidence ref: runs get the caller's name ("Run 3"); other kinds are named by kind, never by a short id. */
function evidenceLabel(kind: string, id: string, runLabel?: (id: string) => string): string {
  if (kind === "experiment") return runLabel ? runLabel(id) : `Run ${id.slice(0, 8)}`;
  return lookup(KIND_LABEL, kind, plainText(kind.replaceAll("_", " "), 40) || "Evidence");
}

/** Evidence refs as labelled links; kinds without a page are shown as plain text. */
export function evidenceLinks(item: Pick<InboxItemLike, "evidence_refs" | "project_id">, runLabel?: (id: string) => string): EvidenceLink[] {
  return item.evidence_refs.map((ref) => ({
    key: `${ref.kind}:${ref.id}`,
    label: evidenceLabel(ref.kind, ref.id, runLabel),
    href: evidenceHref(ref.kind, ref.id, item.project_id),
  }));
}

/** The item's subject as a link when its page exists (a run or a decision record). */
export function subjectLink(item: Pick<InboxItemLike, "subject" | "project_id">, runLabel?: (id: string) => string): EvidenceLink | null {
  const { kind, id } = item.subject;
  if (!id) return null;
  return { key: `${kind}:${id}`, label: evidenceLabel(kind, id, runLabel), href: evidenceHref(kind, id, item.project_id) };
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

/** Who suggested an item. Access-token tools are never "the assistant"; the AI reviewer is its own label. */
export const PROPOSED_BY_LABEL: Record<string, string> = {
  agent: "connected tool (access token)", assistant: "the assistant", jev: "AI reviewer", rule: "the rules", person: "a person", run: "the run",
};

/**
 * "Suggested by the rules" etc. "agent" means two different things by item kind: a decision proposed through the web API
 * is a connected tool (access token); a typed suggestion is a draft from an AI agent run, and the read model does not say
 * who started that run, so it is never called a connected tool or the assistant. Unknown sources keep their own cleaned words.
 */
export function suggestedBy(proposedBy: string | null | undefined, kind?: string): string | null {
  if (!proposedBy) return null;
  if (proposedBy === "agent" && kind === "agent_proposal") return "Suggested by an AI agent (review run)";
  const who = lookup(PROPOSED_BY_LABEL, proposedBy, plainText(proposedBy.replaceAll("_", " "), 24));
  return proposedBy === "run" ? "Raised by the run" : `Suggested by ${who}`;
}

/** A decision question per kind of record, in words a reader answers with Approve or Reject. */
const DECISION_QUESTION: Record<string, string> = {
  ref_moved: "Change what the project uses?", champion_promoted: "Put this model in use?", experiment_accepted: "Accept this run?", experiment_rejected: "Reject this run?",
  winner_locked: "Confirm the best model of this run?", split_plan_created: "Confirm the test design?", problem_spec_locked: "Confirm the goal?",
  ref_initialized: "Confirm the first versions in use?", operating_point_chosen: "Use this threshold?", decision_point_resolved: "Settle this question?",
  proposal_accepted: "Accept this suggestion?", proposal_rejected: "Reject this suggestion?", proposal_reverted: "Undo this suggestion?",
};
/** The question for a decision type, or a cleaned fallback. */
export function inboxQuestion(type: string): string {
  return Object.hasOwn(DECISION_QUESTION, type) ? DECISION_QUESTION[type] : `Decision: ${plainText(type.replaceAll("_", " "), 60) || "unnamed"}`;
}
const PROPOSAL_QUESTION: Record<string, string> = {
  ExperimentPlanProposal: "A suggested experiment", ExperimentReviewProposal: "Review of a run", DatasetInvestigationProposal: "Notes on your data",
  ImprovementActionProposal: "A suggested improvement", ToolCallProposal: "Let the assistant take this action?", ReleaseProposal: "A suggested release",
  SemanticReviewProposal: "A suggested column change",
};
const RUN_WORDS: Record<string, string> = { completed: "finished", failed: "failed", cancelled: "was cancelled", skipped: "was skipped", ended: "ended" };

/** The card's plain title. Run names come from the caller ("Run 3"); the server's fixed summaries are only matched, never shown. */
export function inboxTitle(item: Pick<InboxItemLike, "kind" | "subject"> & { summary: string; decision_type?: string | null; proposal_type?: string | null; status: string }, runName?: string | null): string {
  if (item.kind === "decision_proposal") {
    return inboxQuestion(item.decision_type ?? "");
  }
  if (item.kind === "agent_proposal") {
    const type = item.proposal_type ?? "";
    return Object.hasOwn(PROPOSAL_QUESTION, type) ? PROPOSAL_QUESTION[type] : "A suggestion";
  }
  if (item.kind === "question") return /split/i.test(item.summary) ? "Confirm how this run's test rows are chosen" : "Choose the column this run predicts";
  if (item.kind === "run_finished") {
    const end = /\s(completed|failed|cancelled|skipped|ended)$/.exec(item.summary)?.[1];
    return `${runName ?? "A run"} ${end ? RUN_WORDS[end] : "ended"}`;
  }
  return plainText(item.summary, 100);
}

const OUTCOME_WORDS: Record<string, string> = {
  proposed: "waiting for your decision", accepted: "approved", rejected: "rejected", superseded: "corrected", applied: "applied", reverted: "undone",
  expired: "expired", needs_input: "needs your answer",
};
/** The state of an item in words (run states use the run words). */
export function outcomeWords(status: string): string {
  return Object.hasOwn(OUTCOME_WORDS, status) ? OUTCOME_WORDS[status] : statusWords(status).toLowerCase();
}

/** What approving a typed suggestion does, by its type (what the server's command does for that type). */
const PROPOSAL_APPROVE: Record<string, string> = {
  ExperimentReviewProposal: "Approving records that you read it; nothing changes.",
  DatasetInvestigationProposal: "Approving records that you read it; nothing changes.",
  ImprovementActionProposal: "Approving records that you read it; nothing changes.",
  ExperimentPlanProposal: "Approving marks the plan accepted; a run uses it later.",
  SemanticReviewProposal: "Approving starts a new run with this column change.",
  ToolCallProposal: "Approving runs this action as you.",
};

/** What approving or rejecting does, from the route the server named and the suggestion's type. Nothing here claims more than that. */
export function approvalNote(item: { proposal_type?: string | null }, route: ActionRoute | null, name: string): string | null {
  if (route === null) return null;
  if (name === "reject") return "Rejecting records that you said no. Nothing else changes.";
  if (route === "ref_move_accept") return "Approving changes which version the project uses (for example the model in use). It is recorded in History.";
  if (route === "decision_accept") return "Approving records your decision in History.";
  if (route === "proposal_accept") {
    const type = item.proposal_type ?? "";
    return Object.hasOwn(PROPOSAL_APPROVE, type) ? PROPOSAL_APPROVE[type] : "Approving records your decision. What it does depends on the kind of suggestion.";
  }
  if (route === "proposal_revert") return "Undo puts the previous value back where it can.";
  if (route === "decision_supersede") return "A correction adds a new History entry; the old one stays, marked corrected.";
  return null;
}

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
