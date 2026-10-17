/**
 * Retrain on newest data and editable monitoring thresholds (D5c, ADR 0012): pure view-model logic, so it runs under
 * `npm run test:components`. Every sentence restates fields of `GET /v1/projects/{id}/retrain-status`, `.../retrain-policy`
 * or `.../monitoring-thresholds`; a value that was not returned is left out, never guessed. The comparison is always "same
 * kind of test, latest months" (each model has its own final test). Real outcomes of the model in use are "not the same data".
 * A floor alert is never called drift. The server's own messages (comparison message, refusal codes) are only matched by code,
 * never shown. All times say UTC. No AI anywhere.
 */
import { plainText } from "./command-search.ts";
import { utcWhen } from "./format-time.ts";
import { lowerIsBetter } from "./studio-compare.ts";
import { metricLabel, weekLabel, type OwnStep } from "./studio-monitoring.ts";
import { envelope, type PlainError } from "./studio-wizard.ts";

const own = <T>(table: Record<string, T>, key: string | null | undefined): T | undefined => (typeof key === "string" && Object.hasOwn(table, key) ? table[key] : undefined);
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const count = (v: number) => Math.round(v).toLocaleString("en-US");
const pct = (v: number) => `${Math.round(v * 100)}%`;

export const WORDING = "Same kind of test, latest months";
export const NOT_SAME_DATA = "Not the same data";
export const OWN_FINAL_TEST = "Its final test is its own (a separate test from the model in use). The two can be compared only if that final test is the latest months of the new file and every row is later than the training rows of the model in use.";
export const NOT_CARRIED_FIXED = "Branch settings of the model in use (model families, class weighting, column treatments, model settings, a changed objective) are not carried over; the new run decides them again.";
export const SWITCH_IS_YOURS = "Nothing switches the model in use on its own: a person decides, and it is saved in History.";

/* ---------- reads (shapes the hooks return) ---------- */

export type RetrainSideLike = { metric?: string | null; metric_value?: number | null; metrics?: Record<string, number | null> | null; decision_threshold?: number | null; n_rows?: number | null };
export type RetrainComparisonLike = {
  status: string; reason_code?: string | null; time_column?: string | null; incumbent_training_end?: string | null; final_test_start?: string | null;
  final_test_rows?: number | null; challenger?: RetrainSideLike | null; incumbent?: RetrainSideLike | null; proposal_refusal?: string | null; computed_at?: string;
};
export type RetrainRequestLike = { origin: string; created_at: string; dataset_id: string; incumbent_model_version_id?: string | null; experiment_id?: string | null; experiment_status?: string | null; not_carried?: string[] };
export type RetrainStatusLike = {
  policy: string; state: string; model_in_use_id?: string | null; latest_window_start?: string | null; latest_window_status?: string | null; newer_dataset_id?: string | null;
  request?: RetrainRequestLike | null; comparison?: RetrainComparisonLike | null; comparison_withheld?: boolean;
  real_outcomes?: { window_start: string; precision?: number | null; recall?: number | null; n_labelled?: number | null } | null;
  proposal_id?: string | null; proposal_state?: string | null; can_start?: boolean;
};
export type RetrainPolicyLike = { policy: string; version: number; updated_at?: string | null; updated_by_user_id?: string | null; owner_can_start?: boolean | null; auto_paused_until?: string | null };

/* ---------- the retrain policy ---------- */

export const POLICIES = ["off", "ask", "auto"] as const;
export type PolicyValue = (typeof POLICIES)[number];
const POLICY_WORDS: Record<PolicyValue, { label: string; who: string; text: string }> = {
  off: { label: "Off", who: "Off for this project", text: "DCLab never retrains on its own. A person can still retrain on a newer labelled file." },
  ask: { label: "Ask me", who: "Asks you first", text: "When the newest week of the model in use is an alert and a newer labelled file was uploaded by a person who can start runs, DCLab asks in the Inbox: “Retrain on newest data?”. Nothing starts until a person says yes." },
  auto: { label: "Automatic", who: "Starts on its own", text: "In that case DCLab starts the retrain by itself, acting as the person who chose Automatic. It never switches the model in use." },
};
export const RULE_LIMIT = "The rule acts at most once per project in any 7 days, and never twice on the same file or week.";
export const isPolicy = (v: unknown): v is PolicyValue => typeof v === "string" && (POLICIES as readonly string[]).includes(v);
export function policyWords(policy: string): { label: string; who: string; text: string } {
  return own(POLICY_WORDS, policy) ?? { label: "Unknown", who: "Unknown setting", text: "This project has a retrain setting this page does not recognise, so nothing more is said." };
}

/** Facts under the policy control: who set it, a paused automatic start, an owner who can no longer start runs. */
export function policyFacts(p: RetrainPolicyLike, myUserId: string | null | undefined): string[] {
  const out: string[] = [];
  if (p.version === 0) out.push("Never changed: Off is the default.");
  else {
    const by = p.updated_by_user_id ? (myUserId && p.updated_by_user_id === myUserId ? "you" : "a person") : null;
    out.push(`Set${by ? ` by ${by}` : ""}${p.updated_at && utcWhen(p.updated_at) !== "—" ? ` on ${utcWhen(p.updated_at)}` : ""} (version ${p.version}).`);
  }
  if (p.policy === "auto" && p.owner_can_start === false) out.push("The person who chose Automatic can no longer start runs, so nothing starts automatically until an owner or administrator saves the policy again or changes the policy here.");
  if (p.policy === "auto" && p.auto_paused_until && utcWhen(p.auto_paused_until) !== "—") {
    out.push(`A plan limit refused the last automatic start, so no automatic retrain starts before ${utcWhen(p.auto_paused_until)}. Saving the policy clears this.`);
  }
  return out;
}

/* ---------- "What the system does on its own": the retrain steps ---------- */

const STOPPED: Record<string, string> = { failed: "failed", cancelled: "was cancelled", skipped: "was skipped" };
const REFUSAL: Record<string, string> = {
  model_in_use_changed: "No switch decision was opened: the model in use changed after the retrain started.",
  no_model_version: "No switch decision was opened: the retrained run has no saved model to switch to.",
};
export const REFUSAL_OTHER = "No switch decision was opened for this retrain.";

/** The newest week's alert in words: drift, or real outcomes below a floor (never called drift). */
export function alertFact(s: Pick<RetrainStatusLike, "state" | "latest_window_start">): string | null {
  const week = s.latest_window_start ? weekLabel(s.latest_window_start) : "newest week";
  if (s.state === "drift_found") return `The ${week} is an alert: the inputs or scores look different from training (drift).`;
  if (s.state === "below_floor") return `The ${week} is an alert: real outcomes fell below a floor set for this project. This is not drift.`;
  return null;
}

/**
 * The three retrain steps of "What the system does on its own" (retrain, compare, decide), from the status read. `failed`:
 * the read failed (the state is unknown, never guessed). Nothing here claims an automatic switch.
 */
export function retrainSteps(s: RetrainStatusLike | undefined, failed: boolean): OwnStep[] {
  if (!s) {
    const text = failed ? "The retrain status could not be read, so the state of this step is unknown." : "Loading…";
    return [
      { key: "retrain", title: "Retrain on newest data", who: "Unknown", state: "off", pill: "Unknown", tone: "gray", text },
      { key: "compare", title: "Show the retrained model beside the model in use", who: "Automatic", state: "off", pill: "Unknown", tone: "gray", text },
      { key: "decide", title: "Switch or keep", who: "You decide", state: "yours", pill: "Yours", tone: "gray", text: SWITCH_IS_YOURS },
    ];
  }
  const policy = policyWords(s.policy);
  const req = s.request ?? null;
  const run = req?.experiment_status ?? null;
  const later = ["ready_to_compare", "waiting_for_you", "switched", "kept"].includes(s.state);
  const fact = alertFact(s);
  const newer = s.newer_dataset_id ? "A newer labelled file exists." : "No newer labelled file was uploaded since the model in use was trained.";
  let retrain: Pick<OwnStep, "state" | "pill" | "tone" | "text">;
  if (s.state === "retraining" && req) {
    const by = req.origin === "auto" ? "Started automatically (the retrain policy is Automatic)." : "Started by a person.";
    retrain = req.experiment_status === "needs_input"
      ? { state: "on", pill: "Waiting for an answer", tone: "warn", text: `${by} The retrain run is waiting for an answer in the Inbox before it can go on.` }
      : { state: "on", pill: "Retraining", tone: "warn", text: `${by} A new run is training on the newer labelled file with the same target and goal. ${OWN_FINAL_TEST}` };
  } else if (later) {
    retrain = { state: "on", pill: "Finished", tone: "ok", text: "The retrained run finished." };
  } else if (req && req.origin === "ask" && !req.experiment_id && s.state !== "idle") {
    retrain = { state: "on", pill: "Asked you", tone: "warn", text: `${fact ?? ""} DCLab asked in the Inbox: “Retrain on newest data?”. Nothing starts until a person says yes.`.trim() };
  } else if (req && run && own(STOPPED, run)) {
    retrain = { state: "on", pill: "Did not finish", tone: "crit", text: `The last retrain run ${own(STOPPED, run)}. Nothing changed: the model in use stays in use. Upload a newer labelled file to retrain again.` };
  } else {
    const lead = s.policy === "off" ? "Retraining on its own is off for this project." : `${policy.label}: ${policy.text}`;
    retrain = { state: s.policy === "off" ? "off" : "on", pill: policy.label, tone: "gray", text: [lead, fact, newer].filter(Boolean).join(" ") };
  }
  const c = s.comparison ?? null;
  let compare: Pick<OwnStep, "state" | "pill" | "tone" | "text">;
  if (s.state === "retraining") {
    compare = { state: "on", pill: "Waiting", tone: "gray", text: "When the retrained run finishes, DCLab scores the model in use on the retrained model's final-test rows, only if all of them are later than its training rows: same kind of test, latest months." };
  } else if (later && s.comparison_withheld) {
    compare = { state: "on", pill: "Compared", tone: "gray", text: "The comparison exists; its figures are shown to people only." };
  } else if (later && c) {
    const refusal = s.state === "ready_to_compare" ? ` ${c.proposal_refusal ? own(REFUSAL, c.proposal_refusal) ?? REFUSAL_OTHER : ""}` : "";
    compare = c.status === "compared"
      ? { state: "on", pill: "Compared", tone: "ok", text: `Shown above: ${WORDING.toLowerCase()}.${refusal}`.trim() }
      : { state: "on", pill: "Not comparable", tone: "gray", text: `${notComparableWords(c.reason_code)}.${refusal}` };
  } else if (s.state === "ready_to_compare") {
    compare = { state: "on", pill: "Comparing", tone: "warn", text: "The retrained run finished; DCLab is comparing it with the model in use. Reload in a few minutes." };
  } else {
    compare = { state: "off", pill: "Nothing yet", tone: "gray", text: "Nothing to compare until a retrain finishes." };
  }
  let decide: Pick<OwnStep, "state" | "pill" | "tone" | "text">;
  if (s.state === "waiting_for_you") decide = { state: "yours", pill: "Waiting for you", tone: "warn", text: "Switch to the retrained model or keep the model in use, below or in the Inbox. Nothing switches until a person decides." };
  else if (s.state === "switched" || s.proposal_state === "accepted") decide = { state: "yours", pill: "Switched", tone: "ok", text: "A person switched to the retrained model. It is saved in History." };
  else if (s.state === "kept") decide = { state: "yours", pill: "Kept", tone: "gray", text: "A person kept the model in use. It is saved in History." };
  else decide = { state: "yours", pill: "Yours", tone: "gray", text: `${SWITCH_IS_YOURS} Automatic never switches.` };
  return [
    { key: "retrain", title: "Retrain on newest data", who: policy.who, ...retrain },
    { key: "compare", title: "Show the retrained model beside the model in use", who: "Automatic", ...compare },
    { key: "decide", title: "Switch or keep", who: "You decide", ...decide },
  ];
}

/**
 * Whether to offer "Retrain on newest data": the server's own rule (`can_start`, D8a: a person with workspace ML execution, a
 * model in use, a newer labelled file not yet retrained on, nothing running). The page no longer guesses from the session's
 * capabilities; the start itself is still decided by the server (plan limits may refuse it).
 */
export function canOfferRetrain(s: RetrainStatusLike | undefined): boolean {
  return !!s && s.can_start === true && !!s.newer_dataset_id;
}

/** The confirm step before a retrain starts: what happens, in fixed words. */
/** `notCarried`: the settings of the model in use a new root run re-decides, or null when unknown (the fixed sentence). */
export function retrainConfirmLines(file: { name?: string | null; rows?: number | null; uploadedAt?: string | null } | null, notCarried: readonly string[] | null = null): string[] {
  const name = plainText(file?.name, 80) || "the newest labelled file";
  const facts = [num(file?.rows) !== null ? `${count(file!.rows as number)} rows` : null, file?.uploadedAt && utcWhen(file.uploadedAt) !== "—" ? `uploaded ${utcWhen(file.uploadedAt)}` : null].filter(Boolean).join(" · ");
  const settings = (notCarried ?? []).map((k) => own(NOT_CARRIED, k)).filter(Boolean);
  return [
    `A new run trains on ${name}${facts ? ` (${facts})` : ""} with the same target and goal as the model in use.`,
    OWN_FINAL_TEST,
    ...(notCarried === null ? [NOT_CARRIED_FIXED] : settings.length ? [`Not carried over (the new run decides them again on its own rows): ${settings.join(", ")}.`] : []),
    "When it finishes, DCLab compares it with the model in use if they can be fairly compared (same kind of test, latest months), or says why not, and asks you to switch or keep.",
    "Switching is your choice; nothing changes the model in use by itself. The run counts against your plan's limits like any run.",
  ];
}
const NOT_CARRIED: Record<string, string> = {
  model_families: "the model families", class_weighting: "class weighting", column_treatments: "column treatments", hyperparameters: "model settings", branch_objective: "a changed objective or threshold goal",
};

/* ---------- the challenger card ---------- */

const NOT_COMPARABLE: Record<string, string> = {
  final_test_overlaps_training: "Not comparable: the new final test overlaps the training period of the model in use",
  no_time_column: "Not comparable: the files have no shared date column to show the new final test is later",
  time_missing: "Not comparable: some rows have no readable date, so the new final test cannot be shown to be later",
  time_kinds_differ: "Not comparable: the date column is read differently in the two files",
  data_unavailable: "Not comparable: a training file or its split could not be read",
  incumbent_cannot_score: "Not comparable: the model in use cannot score the new final-test rows",
  target_coding_differs: "Not comparable: the two models read the outcome labels differently",
  challenger_not_scored: "Not comparable: the retrained model has no final-test result",
  not_enough_labels: "Not comparable: too few final-test rows of each class",
  comparison_failed: "Not comparable: the comparison could not be computed",
  challenger_test_not_latest: "Not comparable: the retrained model's final test is not its latest months",
  task_differs: "Not comparable: the two models predict different kinds of target",
};
export const NOT_COMPARABLE_REASONS = Object.keys(NOT_COMPARABLE);
export function notComparableWords(code: string | null | undefined): string {
  return own(NOT_COMPARABLE, code) ?? NOT_COMPARABLE.comparison_failed;
}

export type CompareRow = { label: string; retrained: string; inUse: string };
export type ComparisonView =
  | { kind: "none" } | { kind: "withheld" }
  | { kind: "compared"; rows: CompareRow[]; facts: string[] }
  | { kind: "not_comparable"; reason: string; own: string | null };

const value = (v: unknown, digits = 3) => (num(v) === null ? "—" : (v as number).toFixed(digits));
const metricOf = (side: RetrainSideLike | null | undefined, key: string): number | null => {
  if (!side) return null;
  if (side.metric === key && num(side.metric_value) !== null) return side.metric_value as number;
  return side.metrics && Object.hasOwn(side.metrics, key) ? num(side.metrics[key]) : null;
};
const EXTRA_METRICS = 4;

/** Both models side by side on the retrained model's final-test rows, or the named reason when they cannot be. */
export function comparisonView(c: RetrainComparisonLike | null | undefined, withheld = false): ComparisonView {
  if (withheld) return { kind: "withheld" };
  if (!c) return { kind: "none" };
  if (c.status !== "compared") {
    const ch = c.challenger;
    const v = num(ch?.metric_value);
    const n = num(ch?.n_rows);
    const own = ch?.metric && v !== null
      ? `The retrained model's own final test: ${metricLabel(ch.metric)} ${v.toFixed(3)}${n !== null ? ` on ${count(n)} rows` : ""}. The model in use was not scored on these rows, so nothing is put beside it.`
      : null;
    return { kind: "not_comparable", reason: `${notComparableWords(c.reason_code)}.`, own };
  }
  const ch = c.challenger ?? null;
  const inc = c.incumbent ?? null;
  const primary = ch?.metric ?? inc?.metric ?? null;
  const rows: CompareRow[] = [];
  if (primary) {
    rows.push({ label: `${metricLabel(primary)} (${lowerIsBetter(primary) ? "lower" : "higher"} is better)`, retrained: value(metricOf(ch, primary)), inUse: value(metricOf(inc, primary)) });
  }
  const extra = Object.keys(inc?.metrics ?? {}).filter((k) => k !== primary && metricOf(ch, k) !== null && metricOf(inc, k) !== null).sort().slice(0, EXTRA_METRICS);
  for (const k of extra) rows.push({ label: metricLabel(k), retrained: value(metricOf(ch, k)), inUse: value(metricOf(inc, k)) });
  rows.push({ label: "Threshold (each model's own)", retrained: value(ch?.decision_threshold, 2), inUse: value(inc?.decision_threshold, 2) });
  rows.push({ label: "Final-test rows scored", retrained: num(ch?.n_rows) === null ? "—" : count(ch!.n_rows as number), inUse: num(inc?.n_rows) === null ? "—" : count(inc!.n_rows as number) });
  const col = plainText(c.time_column, 60);
  const start = plainText(c.final_test_start, 40);
  const end = plainText(c.incumbent_training_end, 40);
  const n = num(c.final_test_rows);
  const facts = [
    `Both models were scored on the retrained model's final-test rows${n !== null ? ` (${count(n)})` : ""}, every one later than the training rows of the model in use${col && start && end ? ` (by ${col}: the final test starts ${start}; the model in use was trained on rows up to ${end})` : ""}.`,
    "Each model has its own final test and its own threshold; this is the same kind of test, latest months.",
    "Nothing is fit or chosen on these rows: they are evidence for your decision.",
  ];
  return { kind: "compared", rows, facts };
}

/** The real outcomes of the model in use, shown beside the comparison and labelled "not the same data". */
export function realOutcomesText(r: RetrainStatusLike["real_outcomes"]): string {
  if (!r) return "No real outcomes of the model in use have been measured yet.";
  const parts = [num(r.precision) !== null ? `precision ${(r.precision as number).toFixed(2)}` : null, num(r.recall) !== null ? `recall ${(r.recall as number).toFixed(2)}` : null].filter(Boolean);
  const n = num(r.n_labelled);
  return `Real outcomes of the model in use in the ${weekLabel(r.window_start)}: ${parts.join(", ") || "not measured"}${n !== null ? ` on ${count(n)} rows with a real outcome` : ""}. These are scored rows of the model in use, not the rows above.`;
}

/* ---------- thresholds ---------- */

export type Bounds = { psi_watch_min: number; psi_watch_max: number; psi_alert_max: number; floor_min: number; floor_max: number };
export type ThresholdValues = { psi_watch: number; psi_alert: number; precision_floor: number | null; recall_floor: number | null };
export type ThresholdDraft = { psi_watch: string; psi_alert: string; precision_on: boolean; precision_floor: string; recall_on: boolean; recall_floor: string; rationale: string };
export type DraftField = "psi_watch" | "psi_alert" | "precision_floor" | "recall_floor" | "rationale";
export const RATIONALE_MAX = 2000;

export function draftFrom(v: ThresholdValues): ThresholdDraft {
  return {
    psi_watch: String(v.psi_watch), psi_alert: String(v.psi_alert),
    precision_on: v.precision_floor !== null, precision_floor: v.precision_floor === null ? "" : String(v.precision_floor),
    recall_on: v.recall_floor !== null, recall_floor: v.recall_floor === null ? "" : String(v.recall_floor), rationale: "",
  };
}

const DECIMAL = /^(\d+(\.\d*)?|\.\d+)$/;
const parse = (text: string): number | null => (DECIMAL.test(text.trim()) ? Number(text.trim()) : null);

export function boundsText(b: Bounds): string {
  return `Watch line: from ${b.psi_watch_min} to ${b.psi_watch_max}. Alert line: above the watch line, at most ${b.psi_alert_max}. Each floor: from ${b.floor_min} to ${b.floor_max} (for example 0.6 means 60%), or off.`;
}

/** The form's values within the server's bounds, or one plain sentence per field that is not. */
export function validateThresholds(d: ThresholdDraft, b: Bounds, current: ThresholdValues | null = null):
  { ok: true; values: ThresholdValues } | { ok: false; errors: Partial<Record<DraftField | "form", string>> } {
  const errors: Partial<Record<DraftField | "form", string>> = {};
  const watch = parse(d.psi_watch);
  const alert = parse(d.psi_alert);
  if (watch === null || watch < b.psi_watch_min || watch > b.psi_watch_max) errors.psi_watch = `Enter a number from ${b.psi_watch_min} to ${b.psi_watch_max}.`;
  if (alert === null || alert > b.psi_alert_max || alert <= b.psi_watch_min) errors.psi_alert = `Enter a number above the watch line, at most ${b.psi_alert_max}.`;
  else if (watch !== null && alert <= watch) errors.psi_alert = "The alert line must be above the watch line.";
  const floor = (on: boolean, text: string, key: "precision_floor" | "recall_floor"): number | null => {
    if (!on) return null;
    const v = parse(text);
    if (v === null || v < b.floor_min || v > b.floor_max) errors[key] = `Enter a number from ${b.floor_min} to ${b.floor_max}, or switch this floor off.`;
    return v;
  };
  const precision = floor(d.precision_on, d.precision_floor, "precision_floor");
  const recall = floor(d.recall_on, d.recall_floor, "recall_floor");
  const reason = d.rationale.trim();
  if (!reason) errors.rationale = "Give a reason; it is saved in History.";
  else if (reason.length > RATIONALE_MAX) errors.rationale = `Keep the reason under ${RATIONALE_MAX.toLocaleString("en-US")} characters.`;
  if (Object.keys(errors).length) return { ok: false, errors };
  const values: ThresholdValues = { psi_watch: watch as number, psi_alert: alert as number, precision_floor: precision, recall_floor: recall };
  if (current && thresholdChanges(current, values).length === 0) return { ok: false, errors: { form: "These are already the values in force. Change at least one." } };
  return { ok: true, values };
}

const shown = (v: number | null) => (v === null ? "off" : String(v));
const FIELDS: Array<[keyof ThresholdValues, string]> = [["psi_watch", "Watch line (PSI)"], ["psi_alert", "Alert line (PSI)"], ["precision_floor", "Precision floor"], ["recall_floor", "Recall floor"]];
/** "Watch line (PSI): 0.1 → 0.15" for each value that changes. */
export function thresholdChanges(from: ThresholdValues, to: ThresholdValues): string[] {
  return FIELDS.filter(([k]) => from[k] !== to[k]).map(([k, label]) => `${label}: ${shown(from[k])} → ${shown(to[k])}`);
}
export const THRESHOLDS_APPLY = "Weeks computed with new scored data after you save use the new values; past weeks, and a week recomputed without new scorings, keep the values they used. Nothing is computed again now.";
export const FLOORS_NOT_DRIFT = "A floor is judged only on real outcomes, for yes/no models, when enough labelled rows are linked; a week below a floor is an alert, but it is not drift.";

/** "Set by you, version 3 on Mon, 12 Oct 2026, 09:30 UTC. Weeks computed with new scored data after that ..." or the defaults. */
export function setByText(t: { version: number; changed_at?: string | null; changed_by_user_id?: string | null }, myUserId: string | null | undefined): string {
  if (t.version === 0) return "DCLab defaults: never changed for this project.";
  const by = t.changed_by_user_id ? (myUserId && t.changed_by_user_id === myUserId ? "you" : "a person") : null;
  const when = t.changed_at && utcWhen(t.changed_at) !== "—" ? ` on ${utcWhen(t.changed_at)}` : "";
  return `Set${by ? ` by ${by}` : ""}, version ${t.version}${when}. Weeks computed with new scored data after that use these values; past weeks, and a week recomputed without new scorings, keep the values they used.`;
}
export function valuesText(v: ThresholdValues): string {
  return `watch ${v.psi_watch} · alert ${v.psi_alert} · precision floor ${shown(v.precision_floor)} · recall floor ${shown(v.recall_floor)}`;
}

/** The values a week was computed with; null means before they were recorded (the defaults, no floors). */
export const VALUES_UNAVAILABLE = "values not available (computed before they were recorded)";
export type UsedLike = { psi_watch: number; psi_alert: number; precision_floor?: number | null; recall_floor?: number | null; version?: number };
export function usedText(used: UsedLike | null | undefined, defaults: ThresholdValues | null): string {
  if (used) return valuesText({ psi_watch: used.psi_watch, psi_alert: used.psi_alert, precision_floor: num(used.precision_floor), recall_floor: num(used.recall_floor) });
  return defaults ? `defaults (${valuesText({ ...defaults, precision_floor: null, recall_floor: null })})` : VALUES_UNAVAILABLE;
}

const FLOOR_NOT_JUDGED: Record<string, string> = {
  outcomes_not_measured: "is not judged until enough real outcomes are linked and measured",
  not_binary: "applies to yes/no models only, so it is not judged",
  no_flagged_rows: "is not judged: no labelled row was flagged",
  no_positive_outcomes: "is not judged: no labelled row had the outcome",
  confusion_unavailable: "is not judged: this week's counts of right and wrong flags are not available",
  too_few_flagged_rows: "is not judged: too few labelled rows were flagged",
  too_few_positive_outcomes: "is not judged: too few labelled rows had the outcome",
  within_chance: "is not judged: the value is under the floor, but within what chance allows for this many rows",
};
/** One week's floors in plain words, from the stored checks; never "drift". */
export function floorLines(floors: unknown): string[] {
  if (!floors || typeof floors !== "object" || Array.isArray(floors)) return [];
  const out: string[] = [];
  for (const name of ["precision", "recall"] as const) {
    const check = Object.hasOwn(floors, name) ? (floors as Record<string, unknown>)[name] : null;
    if (!check || typeof check !== "object") continue;
    const c = check as { floor?: unknown; value?: unknown; status?: unknown; reason_code?: unknown };
    const floor = num(c.floor);
    if (floor === null) continue;
    const label = `${name === "precision" ? "Precision" : "Recall"} floor (${pct(floor)})`;
    const v = num(c.value);
    const real = v !== null ? `real ${name} ${pct(v)}` : null;
    if (c.status === "below") out.push(`${label}: ${real ?? "real outcomes"} is below it, clearly so for this many rows. This makes the week an alert; it is not drift.`);
    else if (c.status === "ok") out.push(`${label}: ${real ?? "real outcomes"} is at or above it.`);
    else out.push(`${label} ${own(FLOOR_NOT_JUDGED, typeof c.reason_code === "string" ? c.reason_code : "") ?? FLOOR_NOT_JUDGED.outcomes_not_measured}${real ? ` (${real})` : ""}.`);
  }
  return out;
}

/* ---------- errors: fixed plain sentences by code, then status ---------- */

export type RetrainAction = "start" | "policy" | "thresholds" | "decide";
const COMMON: Record<string, PlainError> = {
  human_session_required: { title: "Only a signed-in person can do this", detail: "A connected tool or an access token cannot.", fixable: false },
  service_token_not_permitted: { title: "Only a signed-in person can do this", detail: "A connected tool or an access token cannot.", fixable: false },
  idempotency_key_conflict: { title: "This may already be saved", detail: "Reload the page and check before you try again.", fixable: false },
  concurrent_conflict: { title: "Another change was being saved", detail: "Try again in a moment.", fixable: false },
};
const BY_CODE: Record<RetrainAction, Record<string, PlainError>> = {
  start: {
    retrain_in_progress: { title: "A retrain is already running", detail: "Wait for it to finish; its result appears on this screen.", fixable: false },
    dataset_not_newer: { title: "That file is not newer", detail: "Only a labelled file uploaded after the training file of the model in use can be used.", fixable: false },
    dataset_already_retrained: { title: "The model in use was already retrained on this file", detail: "Upload a newer labelled file to retrain again.", fixable: false },
    dataset_not_found: { title: "That file was not found", detail: "Reload the page and try again.", fixable: false },
    dataset_not_in_project: { title: "That file belongs to another project", detail: "Upload the file to this project.", fixable: false },
    no_model_in_use: { title: "There is no model in use to retrain", detail: "Approve a model for scoring on the Model screen first.", fixable: false },
    run_quota_exceeded: { title: "Too many runs are running", detail: "Your workspace has as many runs queued or running as it may. Try again when one finishes.", fixable: false },
  },
  policy: {},
  thresholds: {
    thresholds_unchanged: { title: "These are already the values in force", detail: "Change at least one value.", fixable: true },
  },
  decide: {
    invalid_decision_transition: { title: "Already decided", detail: "Someone else decided this first. Reload the page.", fixable: false },
  },
};
const FORBIDDEN: Record<RetrainAction, string> = {
  start: "Starting a retrain needs a role that can run ML work in this workspace.",
  policy: "Only workspace owners and administrators can change the retrain policy.",
  thresholds: "Only workspace owners and administrators can change the thresholds.",
  decide: "Switching or keeping needs a role that can run ML work in this workspace.",
};
const WHAT: Record<RetrainAction, string> = { start: "the retrain", policy: "the retrain policy", thresholds: "the thresholds", decide: "your decision" };

export function mapRetrainError(error: unknown, action: RetrainAction): PlainError {
  const { code } = envelope((error as { body?: unknown } | null)?.body);
  const known = own(BY_CODE[action], code) ?? own(COMMON, code);
  if (known) return known;
  const status = (error as { status?: unknown } | null)?.status;
  if (status === 403) return { title: "You cannot do this here", detail: FORBIDDEN[action], fixable: false };
  if (status === 404) return { title: "Not found", detail: "This project is not in the active workspace, or you cannot see it.", fixable: false };
  if (status === 412 || status === 428 || code === "ref_version_conflict") return { title: "This changed while you were looking", detail: "Reload the page to see the current values, then try again.", fixable: false };
  if (status === 409) return { title: `${WHAT[action][0].toUpperCase()}${WHAT[action].slice(1)} was refused`, detail: "Reload the page and check the current state.", fixable: false };
  if (status === 422 || status === 400) return { title: "Some values are not accepted", detail: action === "thresholds" ? "Check each value against the limits shown and give a reason." : "Check the form and try again.", fixable: true };
  if (status === 429) return { title: "A limit was reached", detail: "Too many requests, or your plan's limit for runs. Try again later.", fixable: false };
  return { title: `Could not confirm ${WHAT[action]}`, detail: "It may have been saved. Reload the page and check before trying again.", fixable: false };
}
