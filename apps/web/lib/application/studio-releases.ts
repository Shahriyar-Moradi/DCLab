/**
 * "Approved for scoring" (V7-B2-UI): pure view-model logic, so it runs under `npm run test:components`.
 * Every sentence restates fields of `GET /v1/projects/{id}/releases`, the refs read, the model version read or the
 * prediction read; a value that was not returned is left out, never guessed. A person approves (putting a model in use
 * records the approval); approving does not retrain or test again. No final test set value is read or formatted here.
 * No sentence built here passes the server's own message through.
 */
import { isUuid, plainText } from "./command-search.ts";
import { modelName } from "./studio-names.ts";
import { envelope, type PlainError } from "./studio-wizard.ts";

/** The reason a person may type (the database keeps up to 4000 characters) and how much of it a row shows. */
export const REASON_MAX = 4000;
export const REASON_SHOWN = 400;
/** The releases read returns at most this many approvals (RELEASE_LIST_MAX in the API). */
export const RELEASES_LISTED = 200;

export type ReleaseLike = {
  id: string; model_version_id: string; model_version: string; approved_at: string; is_current: boolean; saved_format: string;
  decision_record_id: string; approved_by_user_id?: string | null; rationale?: string | null; previous_release_id?: string | null;
};
/** The approval summary carried by a model version read and a prediction read (no person ids). */
export type ReleaseSummaryLike = { id: string; model_version_id: string; approved_at: string; is_current: boolean };

type When = (iso: string) => string;
export const modelLabel = (version: string): string => plainText(modelName(plainText(version, 40)), 60);

export type ReleaseRow = {
  id: string; modelVersionId: string; model: string; by: string; when: string; reason: string | null;
  current: boolean; oldFormat: boolean; goBack: boolean; historyId: string;
  /** "Approved for scoring by you on …" for the current approval; an earlier one never reads as still approved. */
  line: string;
};

/**
 * One row per approval, newest first. "Go back to this model" is offered once per older model (on its newest approval),
 * never on the model in use. The approver is "you" when the id is the signed-in person, otherwise "a person": the API
 * gives no name, so none is made up. Rows whose ids are not UUIDs are dropped.
 */
export function releaseRows(items: readonly ReleaseLike[], opts: { myUserId: string | null; inUseModelId: string | null; when: When }): ReleaseRow[] {
  const valid = items.filter((r) => isUuid(r.id) && isUuid(r.model_version_id));
  const sorted = valid.map((r, i) => ({ r, i })).sort((a, b) => b.r.approved_at.localeCompare(a.r.approved_at) || a.i - b.i).map((x) => x.r);
  const offered = new Set<string>();
  if (opts.inUseModelId) offered.add(opts.inUseModelId);
  for (const r of sorted) if (r.is_current) offered.add(r.model_version_id);
  return sorted.map((r) => {
    const goBack = !r.is_current && !offered.has(r.model_version_id);
    if (goBack) offered.add(r.model_version_id);
    const reason = typeof r.rationale === "string" && r.rationale.trim() ? plainText(r.rationale, REASON_SHOWN) : null;
    const by = opts.myUserId && r.approved_by_user_id === opts.myUserId ? "you" : "a person";
    const when = opts.when(r.approved_at);
    const line = r.is_current
      ? `Approved for scoring by ${by} on ${when}.`
      : r.model_version_id === opts.inUseModelId
        ? `Earlier approval by ${by} on ${when}. This model is in use under a newer approval.`
        : `Earlier approval by ${by} on ${when}. Not the model in use now.`;
    return {
      id: r.id, modelVersionId: r.model_version_id, model: modelLabel(r.model_version), by, when, reason,
      current: r.is_current, oldFormat: r.saved_format === "legacy_quarantined", goBack, historyId: r.decision_record_id, line,
    };
  });
}

type ModelLike = { id: string; version: string; champion: boolean };
/** Models of the project with no approval at all, the model in use first: each can be approved for scoring. */
export function unapprovedModels<M extends ModelLike>(models: readonly M[], items: readonly ReleaseLike[]): M[] {
  const approved = new Set(items.map((r) => r.model_version_id));
  return models.filter((m) => !approved.has(m.id)).sort((a, b) => Number(b.champion) - Number(a.champion));
}

/** Which buttons a viewer sees: none without write access, none while a confirm step is open. */
export function rowAction(row: { goBack: boolean }, viewer: { canWrite: boolean; confirming: boolean }): "go_back" | null {
  return viewer.canWrite && !viewer.confirming && row.goBack ? "go_back" : null;
}
export function modelAction(viewer: { canWrite: boolean; confirming: boolean }): "approve" | null {
  return viewer.canWrite && !viewer.confirming ? "approve" : null;
}
/** The experiment page's put-in-use button: only for a writer, only when this model is not already in use. */
export function canPutInUse(canWrite: boolean, isInUse: boolean): boolean {
  return canWrite && !isInUse;
}

/** A confirm step whose model is no longer offered (someone else approved it meanwhile) is closed so the buttons come back. */
export function targetStillOffered(targetModelId: string, rows: readonly ReleaseRow[], unapproved: readonly { id: string }[]): boolean {
  return rows.some((r) => r.goBack && r.modelVersionId === targetModelId) || unapproved.some((m) => m.id === targetModelId);
}

export type ApprovalSummary =
  | { kind: "loading" } | { kind: "error" }
  | { kind: "approved"; text: string } | { kind: "unrecorded"; text: string } | { kind: "none"; text: string };

/**
 * The first sentence of the tab. Loading, a failed read, an approved model in use, a model in use with no approval
 * recorded (put in use by the first-run rule or before approvals were recorded) and no model in use are distinct.
 * "In use" comes from the project's own reference to the model in use, not from a windowed list of models.
 */
export function approvalSummary(input: {
  pending: boolean; failed: boolean; currentReleaseId: string | null | undefined; rows: readonly ReleaseRow[];
  inUse: { id: string; name: string } | null;
}): ApprovalSummary {
  if (input.failed) return { kind: "error" };
  if (input.pending) return { kind: "loading" };
  const current = input.currentReleaseId ? input.rows.find((r) => r.id === input.currentReleaseId && r.current) : input.rows.find((r) => r.current);
  if (current) return { kind: "approved", text: `${current.model} is the model in use. It was approved for scoring by ${current.by} on ${current.when}.` };
  if (input.inUse) {
    return {
      kind: "unrecorded",
      text: `${input.inUse.name} is the model in use, but no approval is recorded for it: it was put in use by the first-run rule or before approvals were recorded. Approve it for scoring to record one.`,
    };
  }
  return { kind: "none", text: "No model is in use yet, so none is approved for scoring. Approve a model below." };
}

export type ApprovalMode = "approve" | "go_back";

/**
 * What the one confirm step says changes, before anything is sent. `current` is the model in use now (its name), or null
 * when no model is in use. The checks named are the ones the API makes before it records the approval.
 */
export function confirmWords(mode: ApprovalMode, model: string, current: string | null): { title: string; button: string; lines: string[] } {
  const replaces = current === null
    ? "No model is in use now."
    : current === model ? `${model} stays the model in use; this records its approval.` : `${current} stops being the model in use.`;
  return {
    title: mode === "go_back" ? `Go back to ${model}?` : `Approve ${model} for scoring?`,
    button: mode === "go_back" ? `Yes, go back to ${model}` : `Yes, approve ${model}`,
    lines: [
      `${model} becomes the model in use and its features move with it: scoring with the model in use from now on uses ${model}.`,
      replaces,
      "Predictions already made are not changed.",
      "Nothing is retrained or tested again. Before it records the approval, DCLab checks that a saved model file exists in a format DCLab can score with, and that the model was tested on the same test design as the model in use. Each time the model in use scores a file, the approval is checked again.",
      "Scoring on a weekly schedule is planned and not built yet.",
      "The change and your reason are saved in History. A person approves; the assistant can only suggest.",
    ],
  };
}

/** The reason sent when the person leaves it empty (the API needs one): it says that none was given. */
export function reasonToSend(mode: ApprovalMode, model: string, typed: string): string {
  const text = typed.trim().slice(0, REASON_MAX);
  if (text) return text;
  return mode === "go_back" ? `Went back to ${model} on the Model screen (no reason given).` : `Approved ${model} for scoring on the Model screen (no reason given).`;
}

const CONFLICT: PlainError = { title: "Someone else just changed the model in use", detail: "Reload to see the model in use now, then decide again.", fixable: false };
const NOT_ELIGIBLE: PlainError = {
  title: "This model cannot be approved yet",
  detail: "It needs a finished run with a chosen best model, and its features must move with it. Pick another model or train its run again.",
  fixable: false,
};
const BY_CODE: Record<string, PlainError> = {
  model_package_missing: {
    title: "This model has no saved model file",
    detail: "It cannot be approved for scoring. Train its run again (or try a change on it) and approve the new model.",
    fixable: false,
  },
  legacy_model_quarantined: {
    title: "This model was saved in the old format",
    detail: "It can't be approved for scoring until it is trained again. Train its run again (or try a change on it) and approve the new model.",
    fixable: false,
  },
  champion_split_plan_mismatch: {
    title: "Not comparable with the model in use",
    detail: "This model was not tested on the same test design as the model in use. Try a change on the run of the model in use to stay on the same design.",
    fixable: false,
  },
  ref_target_not_found: { title: "This model is not in this project", detail: "Reload the list: it may belong to another project or no longer be listed.", fixable: false },
  idempotency_key_conflict: { title: "Your approval may already be saved", detail: "Reload and check the list before you try again.", fixable: false },
  capability_denied: { title: "You do not have permission to approve a model here", detail: "Approving needs a role that can change ML work in this workspace.", fixable: false },
  decision_actor_not_permitted: { title: "You do not have permission to approve a model here", detail: "Only a person with write access can approve a model.", fixable: false },
  validation_failed: { title: "The reason could not be saved", detail: "Use plain text of up to 4000 characters and try again.", fixable: true },
  release_created_concurrently: CONFLICT,
  stale_ref_version: CONFLICT,
  ref_version_conflict: CONFLICT,
  precondition_failed: CONFLICT,
};
const own = (key: string): PlainError | undefined => (key && Object.hasOwn(BY_CODE, key) ? BY_CODE[key] : undefined);

/** True for refusals after which the same request can never succeed: the next try must be a new action (new key, fresh versions). */
export function isConflict(error: unknown): boolean {
  const status = (error as { status?: unknown } | null)?.status;
  return status === 409 || status === 412 || status === 428;
}

/**
 * Approve / go back refusals in fixed plain sentences. The API sends a decision-record refusal as `code`
 * (`invalid_decision_record`, `ref_version_conflict`, …) with the precise `details.reason` (`legacy_model_quarantined`,
 * `release_created_concurrently`, …); the reason is matched first, then the code, then the status.
 */
export function mapApprovalError(error: unknown): PlainError {
  const { code, details } = envelope((error as { body?: unknown } | null)?.body);
  const reason = typeof details.reason === "string" ? details.reason.toLowerCase() : "";
  const status = (error as { status?: unknown } | null)?.status;
  const known = own(reason) ?? own(code);
  if (known) return known;
  if (reason.startsWith("champion_") || code.startsWith("champion_")) return NOT_ELIGIBLE;
  if (status === 403) return BY_CODE.capability_denied;
  if (status === 412 || status === 428 || status === 409) return CONFLICT;
  if (status === 404) return BY_CODE.ref_target_not_found;
  if (status === 422 || status === 400) return NOT_ELIGIBLE;
  if (status === 429) return { title: "Too many requests at once", detail: "Wait a moment, then try again.", fixable: false };
  return { title: "The approval was not saved", detail: "Nothing changed. Check your connection, reload and try again.", fixable: false };
}

/**
 * One line of the model version page, from its own `release` field (the API fills it only for the model in use when a
 * person approved it). A model not in use now may have been approved earlier: the tab lists those approvals.
 */
export function approvalLine(release: ReleaseSummaryLike | null | undefined, inUse: boolean, when: When): string {
  if (!inUse) return "Not the model in use now (earlier approvals are listed under Approved for scoring)";
  return release ? `Yes, approved on ${when(release.approved_at)}` : "In use, but no approval is recorded";
}

export type ScoringApproval = { lead: string; approval: string | null; stale: string | null };
/**
 * Which model a scoring used, following its status: "Scored with" only once it completed, "Scoring with" while it waits or
 * runs; a failed scoring scored nothing, so it names no approval. With an approval: its date, and when that approval is no
 * longer the current one, which of the two changed. Without one, never a claim of approval: the API records an approval
 * only when the model in use scores and a person approved it. `inUseNow` is whether the model is the model in use now
 * (null when unknown).
 */
export function scoringApproval(status: string, release: ReleaseSummaryLike | null | undefined, when: When, inUseNow: boolean | null): ScoringApproval {
  if (status !== "completed" && status !== "queued" && status !== "running") return { lead: "Was to score with", approval: null, stale: null };
  const lead = status === "completed" ? "Scored with" : "Scoring with";
  if (!release) return { lead, approval: "no approval recorded (it was not an approved model in use when it was sent)", stale: null };
  const stale = release.is_current ? null : inUseNow === false ? NO_LONGER_IN_USE : APPROVAL_NOT_CURRENT;
  return { lead, approval: `approved ${when(release.approved_at)}`, stale };
}
/** "Scored with: Model v2 · approved …" for a scoring card. */
export function scoredWithText(s: ScoringApproval, model: string): string {
  return `${s.lead}: ${model}${s.approval ? ` · ${s.approval}` : ""}`;
}
export const NO_LONGER_IN_USE = "This model is no longer the model in use.";
export const APPROVAL_NOT_CURRENT = "The approval it ran with is no longer the current one.";
