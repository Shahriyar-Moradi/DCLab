/**
 * History in plain words (V7-A5): who did what, about which item, and whether a person may correct it. Pure, so it runs under
 * `npm run test:components`. Everything restates fields the decision list or read already returned; a type or kind this table does
 * not know is shown cleaned, never hidden and never called "a rule". Lookups are own-key checks (a type `__proto__` is just unknown).
 */
import { questionEventWords, studioQuestionHref } from "./business-questions.ts";
import { plainText, projectHref } from "./command-search.ts";
import { utcWhen } from "./format-time.ts";
import { ENGINE_OWNED_TYPES } from "./studio-compare.ts";
import { actorWords } from "./studio-goal.ts";
import { shortId } from "./studio-graph.ts";

const own = <T>(table: Record<string, T>, key: string): T | undefined => (Object.hasOwn(table, key) ? table[key] : undefined);
const cap = (text: string): string => (text ? text[0].toUpperCase() + text.slice(1) : text);

/** One plain sentence per decision type (closed table). Evidence is judged by the people reading it, so no outcome is claimed. */
const WHAT: Record<string, string> = {
  winner_locked: "Chose the best model of a run on cross-validation",
  split_plan_created: "Created the test design",
  ref_initialized: "Set the first versions in use",
  problem_spec_locked: "Locked the goal",
  ref_moved: "Changed a version in use",
  champion_promoted: "Put a model in use",
  experiment_accepted: "Accepted a run",
  experiment_rejected: "Rejected a run",
  proposal_accepted: "Accepted a suggestion",
  proposal_rejected: "Rejected a suggestion",
  proposal_reverted: "Undid a suggestion",
  decision_point_resolved: "Settled a question with the rules and the assistant",
  scoring_schedule_changed: "Changed weekly scoring",
  monitoring_outcomes_linked: "Linked real outcomes",
  operating_point_chosen: "Recorded a preferred threshold",
  retrain_policy_changed: "Changed the retrain policy",
  monitoring_thresholds_changed: "Changed the monitoring thresholds",
};

/** What happened, in a sentence. An unknown type keeps its own words (cleaned) instead of a guess. */
export function whatWords(type: string): string {
  return own(WHAT, type) ?? `Recorded: ${plainText(type.replaceAll("_", " "), 60) || "an entry"}`;
}

const STATE_WORDS: Record<string, string> = { proposed: "waiting for your answer", accepted: "in effect", rejected: "rejected", superseded: "corrected" };
export const stateWords = (state: string): string => own(STATE_WORDS, state) ?? plainText(state.replaceAll("_", " "), 40);

/** What a record is about when the item itself is not named. */
const SUBJECT_WORDS: Record<string, string> = {
  project: "the project", problem_spec: "the goal", dataset_version: "a data version", split_plan: "the test design",
  feature_recipe: "the features", experiment: "a run", candidate: "a candidate model", model_version: "a model",
};

type Subject = { kind: string; id: string };
/** The subject's name when the lineage knows it ("Run 3", "Model v2"), else a run's short reference, else the kind in words. */
export function subjectName(subject: Subject, names: ReadonlyMap<string, string>): string {
  const known = names.get(`${subject.kind}:${subject.id}`);
  if (known) return known;
  if (subject.kind === "experiment") return `Run ${shortId(subject.id)}`;
  return own(SUBJECT_WORDS, subject.kind) ?? plainText(subject.kind.replaceAll("_", " "), 40);
}

type Actor = { kind: string; user_id?: string | null; agent_run_id?: string | null; service_token_id?: string | null };

/** Who, from the record. A person is "you" only when the record's user is the signed-in user. Never "a rule" for an unknown kind. */
export function whoWords(actor: Actor, myUserId: string | null | undefined): string {
  if (actor.kind === "human" && actor.user_id && myUserId && actor.user_id === myUserId) return "You";
  return cap(actorWords(actor));
}

export type Correction =
  | { kind: "answer"; label: "Answer" }
  | { kind: "correct"; label: "Correct" }
  | { kind: "note"; text: string }
  | { kind: "approvals"; text: string; label: string }
  | { kind: "none" };

/** The shortcut for undoing an approval: the Model screen's "Approved for scoring" tab (going back there adds a new entry). */
export const APPROVALS_SHORTCUT = { kind: "approvals", text: "To undo it, go back to an earlier approved model, when there is one, on", label: "Model → Approved for scoring" } as const;
export function approvalsHref(projectId: string): string | null {
  const href = projectHref(projectId, "models");
  return href ? `${href}?tab=approved` : null;
}

const REF_MOVES = new Set(["ref_moved", "champion_promoted", "ref_initialized"]);
/** Types a person may record and correct (mirrors the API's human-recordable types). Anything else is never offered a Correct button. */
const PERSON_CORRECTABLE = new Set(["experiment_accepted", "experiment_rejected"]);
/** Outcomes of a suggestion or of an AI answer: the real undo is the suggestion's Revert in the Inbox, not a new entry here. */
const SUGGESTION_OUTCOMES = new Set(["proposal_accepted", "proposal_reverted", "decision_point_resolved"]);

/**
 * Whether the signed-in person may answer or correct an entry: a waiting suggestion is answered, an entry a person recorded
 * is corrected (a new entry that supersedes it), and anything else says where it is changed or gets nothing.
 */
export function correctionFor(type: string, state: string): Correction {
  if (state === "proposed") return { kind: "answer", label: "Answer" };
  if (state !== "accepted") return { kind: "none" };
  // An approval for scoring (the only type that records one) is undone by going back on the Model screen.
  if (type === "champion_promoted") return { ...APPROVALS_SHORTCUT };
  if (REF_MOVES.has(type)) return { kind: "note", text: "Change the version in use again; that adds a new entry." };
  if (type === "operating_point_chosen") return { kind: "note", text: "Change it from the model's Threshold tab." };
  if (type === "retrain_policy_changed" || type === "monitoring_thresholds_changed") return { kind: "note", text: "An owner or administrator changes it again on Monitoring; that adds a new entry." };
  if (type === "problem_spec_locked") return { kind: "note", text: "Change the goal by locking a new version of it." };
  if (SUGGESTION_OUTCOMES.has(type)) return { kind: "note", text: "Undo it from the suggestion in the Inbox (Revert)." };
  if (ENGINE_OWNED_TYPES.has(type)) return { kind: "note", text: "Recorded by the rules; only the rules change it." };
  // Fail closed: only the types a person records can be corrected here; an unknown type gets nothing.
  return PERSON_CORRECTABLE.has(type) ? { kind: "correct", label: "Correct" } : { kind: "none" };
}

/** Evidence in words: "Run 3", "Model v2", "History entry", with the scope of the number when the record names one. */
const EVIDENCE_WORDS: Record<string, string> = {
  experiment: "a run", candidate: "a candidate model", model_version: "a model", dataset_version: "a data version", split_plan: "the test design",
  problem_spec: "the goal", feature_recipe: "the features", decision_record: "an earlier History entry", agent_run: "an assistant run",
};
export function evidenceName(ref: Subject, names: ReadonlyMap<string, string>): string {
  const known = names.get(`${ref.kind}:${ref.id}`);
  if (known) return known;
  if (ref.kind === "experiment") return `Run ${shortId(ref.id)}`;
  return own(EVIDENCE_WORDS, ref.kind) ?? plainText(ref.kind.replaceAll("_", " "), 40);
}

const EVIDENCE_PAGES: Record<string, string> = {
  experiment: "experiments", model_version: "models", dataset_version: "data", split_plan: "splits", feature_recipe: "features", decision_record: "decisions",
};
/** A link to the evidence when it has a page of its own (UUIDs only, same project); a decision record opens in History. */
export function evidenceHref(projectId: string, ref: Subject): string | null {
  const page = own(EVIDENCE_PAGES, ref.kind);
  if (!page) return null;
  const href = projectHref(projectId, page, ref.id);
  return href && ref.kind === "decision_record" ? `${href.replace(`/decisions/${ref.id}`, "/decisions")}?record=${ref.id}` : href;
}

/* ---------- events from the activity read (V7 UI-E) ---------- */

/** The activity kinds History adds to its log, beside the decision entries (decisions themselves come from the decision list). */
export const LOG_EVENT_KINDS: ReadonlySet<string> = new Set([
  "dataset_uploaded", "final_test_run", "drift_alert", "scheduled_scoring", "retrain_requested", "retrain_compared",
  "question_asked", "question_replied", "question_closed",
]);

export type ActivityEventLike = {
  id: string; kind: string; occurred_at: string; status?: string | null;
  actor: { kind: string; is_you?: boolean | null }; subject: { kind: string; id?: string | null }; link: { kind: string; id: string };
};
export type EventRow = {
  id: string; kind: string; at: string; who: string; whoKind: "rule" | "agent" | "human";
  what: string; about: string | null; href: string | null; linkLabel: string | null; undo: string;
};

const SCHEDULE_WORDS: Record<string, string> = {
  queued: "Weekly scoring started", failed_to_queue: "Weekly scoring could not start", skipped_no_new_file: "Weekly scoring skipped: no new file to score",
  skipped_no_model_in_use: "Weekly scoring skipped: no model in use", skipped_no_approval: "Weekly scoring skipped: the model in use is not approved",
  skipped_owner_not_allowed: "Weekly scoring skipped: the person who set it up can no longer run it", skipped_missed: "Weekly scoring skipped: the time was missed",
};
const UPLOAD_WORDS: Record<string, string> = { training: "Uploaded a training file", scoring: "Uploaded a file to score" };
const RETRAIN_WORDS: Record<string, string> = {
  person: "Started a retrain on newest data", auto: "Started a retrain on newest data automatically (the retrain policy is Automatic)", ask: "Asked in the Inbox: retrain on newest data?",
};

function eventWho(actor: ActivityEventLike["actor"]): Pick<EventRow, "who" | "whoKind"> {
  if (actor.kind === "person") return { who: actor.is_you === true ? "You" : "A person", whoKind: "human" };
  if (actor.kind === "agent") return { who: "A connected tool (access token)", whoKind: "agent" };
  if (actor.kind === "rule") return { who: "The rules", whoKind: "rule" };
  return { who: "Someone else", whoKind: "human" };
}

/**
 * One activity event in a plain sentence, with where to look. Only the four log kinds are shown (null otherwise); a status
 * this table does not know keeps a fixed sentence. Ids are UUID-checked through `projectHref`; nothing from the server's own
 * summary is shown. No final test value is read: the final test event only says it ran once.
 */
export function eventRow(e: ActivityEventLike, projectId: string, names: ReadonlyMap<string, string>): EventRow | null {
  if (!LOG_EVENT_KINDS.has(e.kind)) return null;
  const base = { id: e.id, kind: e.kind, at: e.occurred_at, ...eventWho(e.actor) };
  const subjectId = e.subject.id ?? "";
  switch (e.kind) {
    case "dataset_uploaded":
      return { ...base, what: own(UPLOAD_WORDS, e.status ?? "") ?? "Uploaded a file", about: names.get(`dataset_version:${e.link.id}`) ?? null,
        href: evidenceHref(projectId, { kind: "dataset_version", id: e.link.id }), linkLabel: "Open the file", undo: "Nothing to undo; a new upload adds a new file." };
    case "final_test_run": {
      // Named only when the lineage knows the run ("Run 3"); otherwise "a run" (its id stays behind the link).
      const run = names.get(`experiment:${subjectId || e.link.id}`) ?? null;
      return { ...base, what: `Scored the final test set once for ${run ?? "a run"}`, about: run,
        href: evidenceHref(projectId, { kind: "experiment", id: e.link.id }), linkLabel: run ? `Open ${run}` : "Open the run", undo: "Cannot be repeated for this run." };
    }
    case "drift_alert": {
      // The event does not say whether drift or a floor on real outcomes raised it, so it names both.
      const model = names.get(`model_version:${subjectId}`) ?? "the model";
      return { ...base, what: `Weekly monitoring marked a week as alert for ${model}: drift, or real outcomes below a floor`, about: model,
        href: projectHref(projectId, "monitoring"), linkLabel: "Open Monitoring", undo: "Nothing to undo: it is a check. What to do about it is a person's choice." };
    }
    case "retrain_requested": {
      const run = e.subject.kind === "experiment" && subjectId ? names.get(`experiment:${subjectId}`) ?? null : null;
      return { ...base, what: own(RETRAIN_WORDS, e.status ?? "") ?? "Retrain on newest data", about: run,
        href: e.subject.kind === "experiment" && subjectId ? evidenceHref(projectId, { kind: "experiment", id: subjectId }) : projectHref(projectId, "monitoring"),
        linkLabel: e.subject.kind === "experiment" && subjectId ? (run ? `Open ${run}` : "Open the retrained run") : "Open Monitoring", undo: "Switching is a separate decision; nothing changed the model in use." };
    }
    case "retrain_compared":
      return { ...base, what: e.status === "compared" ? "Put the retrained model beside the model in use (same kind of test, latest months)" : "Could not compare the retrained model with the model in use", about: null,
        href: projectHref(projectId, "monitoring"), linkLabel: "Open Monitoring", undo: "Nothing to undo: it is evidence for a person's decision." };
    case "question_asked":
    case "question_replied":
    case "question_closed":
      return { ...base, what: questionEventWords(e.kind, e.status) ?? "A question to the business", about: null,
        href: e.link.kind === "business_question" ? studioQuestionHref(projectId, e.link.id) : null, linkLabel: "Open the question",
        undo: e.kind === "question_closed" ? "Closing is final; ask a new question to continue." : "Questions and replies are kept as written; nothing is edited." };
    default:
      return { ...base, what: own(SCHEDULE_WORDS, e.status ?? "") ?? "Weekly scoring ran", about: null,
        href: projectHref(projectId, "predictions"), linkLabel: "Open Predictions", undo: "—" };
  }
}

export type LogRow<D> = { type: "decision"; at: string; key: string; item: D } | { type: "event"; at: string; key: string; event: EventRow };
/**
 * The History log: every decision entry the list returned, plus the events, newest first. When the decision list was cut at
 * 100, events older than its oldest entry are left out (so no stretch shows events without their decisions); when the
 * activity read was cut, the note says from when events are listed.
 */
export function mergeLog<D extends { id: string; recorded_at: string }>(
  decisions: readonly D[], decisionsCut: boolean, events: readonly EventRow[], activityOldest: string | null,
): { rows: Array<LogRow<D>>; note: string | null } {
  const oldestDecision = decisions.reduce<string | null>((min, d) => (min === null || d.recorded_at < min ? d.recorded_at : min), null);
  const shown = events.filter((e) => !(decisionsCut && oldestDecision !== null && Date.parse(e.at) < Date.parse(oldestDecision)));
  const rows: Array<LogRow<D>> = [
    ...decisions.map((d) => ({ type: "decision" as const, at: d.recorded_at, key: `decision:${d.id}`, item: d })),
    ...shown.map((e) => ({ type: "event" as const, at: e.at, key: `event:${e.id}`, event: e })),
  ].sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0));
  const note = activityOldest && utcWhen(activityOldest) !== "—"
    ? `Uploads, final tests, alerts, weekly scorings, retrains and questions to the business are listed from ${utcWhen(activityOldest)}; older ones are not shown here.`
    : null;
  return { rows, note };
}

export type NewerFileLike = { count: number; newest_created_at: string; newest: { kind: string; id: string } };
/** The "newer file exists" banner: a training file newer than the one the project uses (nothing is marked old by it). */
export function newerFileText(n: NewerFileLike | null | undefined, modelInUse: boolean | null = null): string | null {
  if (!n || typeof n.count !== "number" || n.count < 1) return null;
  const when = utcWhen(n.newest_created_at);
  const lead = n.count === 1
    ? `A training file newer than the one this project uses was uploaded${when !== "—" ? ` on ${when}` : ""}.`
    : `${n.count.toLocaleString("en-US")} training files newer than the one this project uses were uploaded; the newest${when !== "—" ? ` on ${when}` : ""}.`;
  const scoring = modelInUse === true ? " The model in use keeps scoring as before." : "";
  const monitoring = modelInUse === true ? " Monitoring shows whether the data the model in use scores now looks different from training." : "";
  return `${lead}${scoring} Nothing is marked built on an older version until a person moves the project to a newer file.${monitoring}`;
}
