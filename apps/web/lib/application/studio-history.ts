/**
 * History in plain words (V7-A5): who did what, about which item, and whether a person may correct it. Pure, so it runs under
 * `npm run test:components`. Everything restates fields the decision list or read already returned; a type or kind this table does
 * not know is shown cleaned, never hidden and never called "a rule". Lookups are own-key checks (a type `__proto__` is just unknown).
 */
import { plainText, projectHref } from "./command-search.ts";
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
  operating_point_chosen: "Recorded a preferred threshold",
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
  | { kind: "none" };

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
  if (REF_MOVES.has(type)) return { kind: "note", text: "Change the version in use again; that adds a new entry." };
  if (type === "operating_point_chosen") return { kind: "note", text: "Change it from the model's Threshold tab." };
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
