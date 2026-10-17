/**
 * Inbox view models (P4.16-UI). Pure: no React, no fetch. The API decides who may act; `can_act` / `allowed` only
 * shape the UI. Every id is UUID-checked before it becomes a link and every link goes through `safeInternalHref`.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid, plainText } from "./command-search.ts";
import { BUSINESS_QUESTIONS_HREF, studioQuestionHref } from "./business-questions.ts";
import { improveNoticeHref } from "./studio-improve-loop.ts";
import { alertCause, weekKey, weekLabel } from "./studio-monitoring.ts";
import { statusWords } from "./studio-runs.ts";
import { envelope, mapWizardError, type PlainError } from "./studio-wizard.ts";

/** Lookup that never reads inherited members: a key like `constructor` or `__proto__` falls back. */
export function lookup<T>(table: Record<string, T>, key: string | null | undefined, fallback: T): T {
  return typeof key === "string" && Object.hasOwn(table, key) ? table[key] : fallback;
}

export type InboxTabId = "needs_decision" | "applied_automatically" | "done";

export const INBOX_TABS: Array<{ id: InboxTabId; label: string; empty: string }> = [
  { id: "needs_decision", label: "Needs your decision", empty: "Nothing is waiting for you. Suggestions from the rules, the assistant, connected tools and people, runs that ask you a question, and drift alerts on the model in use show up here." },
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
  // D5c: Switch = the existing ref-move approval of the retrain rule's proposal; Keep = reject it.
  "challenger_ready|accept": [["POST /v1/projects/{project_id}/refs/{ref_kind}", "ref_move_accept"]],
  "challenger_ready|reject": [["POST /v1/decisions/{decision_id}/reject", "decision_reject"]],
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
  if (!viewerCanDecide) return "Your role can read the inbox but not decide. Ask a data scientist or an admin.";
  if (item.kind === "agent_proposal" && name === "accept") return "Only the person whose conversation made this assistant suggestion can approve it. You can still reject it.";
  return "You cannot take this action here.";
}

export type EvidenceLink = { key: string; label: string; href: string | null };

const KIND_LABEL: Record<string, string> = {
  experiment: "Run", decision_record: "History entry", agent_run: "AI agent run", semantic_answer: "Column review answer", project: "Project",
  model_version: "The model",
};

function evidenceHref(kind: string, id: string, projectId: string | null | undefined): string | null {
  if (!isUuid(projectId) || !isUuid(id)) return null;
  if (kind === "experiment") return safeInternalHref(`/projects/${projectId}/experiments/${id}`);
  if (kind === "decision_record") return safeInternalHref(`/projects/${projectId}/decisions?record=${id}`);
  if (kind === "model_version") return safeInternalHref(`/projects/${projectId}/models/${id}`);
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
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  if (code === "owner_only") return { title: "Only the owner can do this", detail: "Only the person whose conversation this came from can do this. An approver can still reject it.", fixable: false };
  if (status === 428 || code === "ref_versions_required") return { title: "The page is out of date", detail: "The versions currently in use were not available. Reload the inbox and try again.", fixable: false };
  if (status === 412 || code === "ref_version_conflict" || code === "precondition_failed") return { title: "This changed while you were looking", detail: "Reload the inbox to see the current state, then decide again.", fixable: false };
  if (status === 403) return { title: "You cannot do this here", detail: "Deciding needs a role that can write ML work in this workspace. The API enforces this; the disabled button is only a hint.", fixable: false };
  if (status === 404) return { title: "This item is gone", detail: "It is not in this workspace any more, or you cannot see it. Reload the inbox.", fixable: false };
  if (status === 409 || code === "invalid_decision_transition") return { title: "Already decided", detail: "Someone else resolved this first, or it expired. Reload the inbox.", fixable: false };
  return mapWizardError(error);
}

export const INBOX_BADGE_REFETCH_MS = 60_000;

export function badgeText(count: number | undefined): string | null {
  if (!count || count < 1) return null;
  return count > 99 ? "99+" : String(count);
}

/** Who suggested an item. Access-token tools are never "the assistant"; the AI reviewer is its own label. */
export const PROPOSED_BY_LABEL: Record<string, string> = {
  agent: "connected tool (access token)", assistant: "the assistant (Beta)", jev: "AI reviewer", rule: "the rules", person: "a person", run: "the run",
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
export function inboxTitle(item: Pick<InboxItemLike, "kind" | "subject"> & { summary: string; decision_type?: string | null; proposal_type?: string | null; status: string; tab?: string | null; monitoring?: InboxMonitoringLike | null }, runName?: string | null): string {
  // D8b: the week's `summary_code` says which cause raised the alert; without it the title names neither.
  if (item.kind === "drift_alert") {
    const cause = alertCause(item.monitoring?.summary_code);
    const what = cause === "below_floor" ? "Real outcomes below a floor" : cause === "unknown" ? "Alert" : "Drift alert";
    return item.tab === "done" ? `${what} on an earlier week or model` : `${what} on the model in use`;
  }
  if (item.kind === "retrain_ask") return item.tab === "done" || item.status === "closed" ? "Retrain on newest data? (answered or no longer current)" : "Retrain on newest data?";
  if (item.kind === "challenger_ready") return lookup(CHALLENGER_TITLE, item.status, "Retrained model: decided");
  if (item.kind === "improve_run") return lookup(IMPROVE_TITLE, item.status, "Improvement loop stopped");
  if (item.kind === "question_asked") return "A question from the data team";
  if (item.kind === "question_answered") return "A business user replied to your question";
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

const IMPROVE_TITLE: Record<string, string> = {
  waiting_user: "Improvement loop waiting for you", blocked: "Improvement loop stopped: a trust check blocks it", failed: "Improvement loop failed",
};
const CHALLENGER_TITLE: Record<string, string> = { proposed: "Retrained model ready to compare", accepted: "Switched to the retrained model", rejected: "Kept the model in use" };

const OUTCOME_WORDS: Record<string, string> = {
  proposed: "waiting for your decision", accepted: "approved", rejected: "rejected", superseded: "corrected", applied: "applied", reverted: "undone",
  expired: "expired", needs_input: "needs your answer", alert: "alert", asked: "waiting for your answer", closed: "closed",
  waiting_user: "waiting for your decision", blocked: "blocked", open: "waiting for a reply", answered: "answered",
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
export function approvalNote(item: { proposal_type?: string | null; kind?: string }, route: ActionRoute | null, name: string): string | null {
  if (route === null) return null;
  if (item.kind === "challenger_ready") {
    return name === "reject" ? "Keep records that you said no: the model in use stays, and nothing is deleted." : "Switch makes the retrained model the model in use, with its data, test design and features. It is recorded in History.";
  }
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

/* ---------- V7 UI-E: drift alerts, agreement and who closed an item ---------- */

export type InboxMonitoringLike = { window_start?: string | null; max_psi?: number | null; prediction_psi?: number | null; summary_code?: string | null };
const finite = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** The evidence stats of a drift alert, from the item's own aggregates only (no rows, no column names). */
export function driftStats(m: InboxMonitoringLike | null | undefined): Array<{ label: string; value: string }> {
  if (!m) return [];
  const stats: Array<{ label: string; value: string }> = [];
  if (weekKey(m.window_start)) stats.push({ label: "Week (UTC)", value: weekLabel(m.window_start).replace(/^week of /, "").replace(/ \(UTC\)$/, "") });
  if (finite(m.max_psi)) stats.push({ label: "Largest input change of any column (PSI)", value: m.max_psi.toFixed(2) });
  if (finite(m.prediction_psi)) stats.push({ label: "Score change (PSI)", value: m.prediction_psi.toFixed(2) });
  return stats;
}

/** What a drift alert means, in fixed words; the newest week of the model in use waits for a person, earlier ones are done. */
export function driftSentence(item: { tab: string; monitoring?: InboxMonitoringLike | null }): string {
  const week = weekKey(item.monitoring?.window_start) ? `the ${weekLabel(item.monitoring?.window_start)}` : "a week";
  const cause = alertCause(item.monitoring?.summary_code);
  const lead = cause === "below_floor"
    ? `Weekly monitoring marked ${week} as alert: the real outcomes of this model fell below a precision or recall floor set for this project. This is not drift.`
    : cause === "inputs_moved" ? `Weekly monitoring marked ${week} as alert: the inputs of this model look different from training (drift).`
      : cause === "scores_moved" ? `Weekly monitoring marked ${week} as alert: the scores of this model look different from training (drift).`
        : cause === "inputs_and_scores_moved" ? `Weekly monitoring marked ${week} as alert: the inputs and the scores of this model look different from training (drift).`
          : `Weekly monitoring marked ${week} as alert: the inputs or the scores of this model look different from training (drift), or its real outcomes fell below a floor set for this project.`;
  const meaning = cause === "unknown"
    ? "Monitoring says which. It does not mean the model is wrong, and nothing switched the model in use."
    : "Nothing switched the model in use.";
  return item.tab === "done"
    ? `${lead} ${meaning} This is an earlier week, or a model that is no longer in use; Monitoring has the latest status.`
    : `${lead} ${meaning} ${cause === "below_floor" ? "Open Monitoring to see the real outcomes week by week" : "Open Monitoring to see which columns moved"}, then decide what to do; going back to an earlier model is your choice.`;
}

/** D5c: what the retrain rule's question and the retrained model's card mean, in fixed words. */
export function retrainItemSentence(item: { kind: string; tab: string; status: string }): string | null {
  if (item.kind === "retrain_ask") {
    return item.tab === "done" || item.status === "closed"
      ? "The retrain rule asked whether to retrain on a newer labelled file. It was answered, or the model in use changed since."
      : "The newest week of the model in use is an alert and a person uploaded a newer labelled file. The retrain policy is Ask me, so nothing started. Open Monitoring to retrain on that file; switching stays your choice.";
  }
  if (item.kind === "challenger_ready") {
    if (item.status === "proposed") return "A retrained model finished. Monitoring shows it beside the model in use (same kind of test, latest months) or why they could not be compared. Switch to it or keep the model in use.";
    return "This retrained model was decided. Monitoring shows the comparison.";
  }
  return null;
}

/** D4e: what an improvement-loop notice means, and the loop view it opens (UUIDs only; null for any other kind). */
export function improveItemSentence(item: { kind: string; status: string }): string | null {
  if (item.kind !== "improve_run") return null;
  if (item.status === "waiting_user") return "A try beat the noise on cross-validation. Open the loop to accept it (one final run, one final-test number) or keep the current model.";
  if (item.status === "blocked") return "A trust check of the starting run blocks the loop. Nothing was changed.";
  return "The loop stopped without a change. Open it to see why.";
}
export function improveItemHref(item: { kind?: unknown; project_id?: unknown; source?: { kind?: unknown; id?: unknown } | null }): string | null {
  return improveNoticeHref(item);
}

/**
 * D7b: questions to the business, person to person. Fixed words only (the question text stays on its page).
 * Note: the API gives `question_asked` items to business users, who cannot open the Studio Inbox today (it needs
 * development access); they see open questions in the Business view. The card is kept for when they can.
 */
export function businessQuestionSentence(item: { kind: string }): string | null {
  if (item.kind === "question_asked") return "The data team asked the business users of this workspace a question about this project. Open it to read it and reply.";
  if (item.kind === "question_answered") return "A business user replied to a question you asked. Open it to read the reply, add a follow-up or close it.";
  return null;
}
/** Who raised a question card, in fixed words. */
export function businessQuestionBy(kind: string): string | null {
  return kind === "question_asked" ? "Asked by a person on the data team" : kind === "question_answered" ? "Replied by a business user" : null;
}
/**
 * Where a question card opens: the Business view's questions for `question_asked` (business users reply there), the
 * project's "Ask the business" page for `question_answered`. Only when the source is a business question with UUIDs.
 */
export function businessQuestionHref(item: { kind?: unknown; project_id?: unknown; source?: { kind?: unknown; id?: unknown } | null }): string | null {
  if (item.source?.kind !== "business_question" || !isUuid(item.source.id) || !isUuid(item.project_id)) return null;
  if (item.kind === "question_asked") return safeInternalHref(BUSINESS_QUESTIONS_HREF);
  if (item.kind === "question_answered") return studioQuestionHref(item.project_id, item.source.id);
  return null;
}

/** Under the stats: the numbers are the week's largest changes; whether each column counts is decided on Monitoring. */
export const DRIFT_STATS_NOTE = "These are the week's largest changes. Which columns count as changed (the statistical test, column by column) is on Monitoring.";

export function monitoringHref(projectId: string | null | undefined): string | null {
  return isUuid(projectId) ? safeInternalHref(`/projects/${projectId}/monitoring`) : null;
}

const AGREEMENT: Record<string, { label: string; tone: "ok" | "warn" | "gray" }> = {
  agree: { label: "The rules and the AI agreed", tone: "ok" },
  disagree: { label: "The rules and the AI disagreed", tone: "warn" },
  abstain: { label: "The AI gave no answer", tone: "gray" },
  unavailable: { label: "No AI answer was available", tone: "gray" },
};
/**
 * "Both agreed" / "they disagreed: we asked you" from `agreement`, as recorded. Hidden whenever AI is known to be switched off
 * (the AI parts of the Inbox are switched off with it) and for values this table does not know.
 */
export function agreementPill(agreement: string | null | undefined, tab: string, aiOff: boolean | null): { label: string; tone: "ok" | "warn" | "gray" } | null {
  if (aiOff === true || !agreement) return null;
  const found = lookup(AGREEMENT, agreement, null);
  if (!found) return null;
  return agreement === "disagree" && tab === "needs_decision" ? { ...found, label: "The rules and the AI disagreed: we asked you" } : found;
}

/** Who closed a Done item, without ids: you, a person, the rules, or an agent / connected tool. Nothing when it expired. */
export function resolverPill(resolved: { kind: string; is_you?: boolean | null } | null | undefined, tab: string): string | null {
  if (tab !== "done" || !resolved) return null;
  if (resolved.kind === "person") return resolved.is_you === true ? "Decided by you" : "Decided by a person";
  if (resolved.kind === "rule") return "Settled by the rules";
  if (resolved.kind === "agent") return "Settled by an AI agent or a connected tool";
  return null;
}
