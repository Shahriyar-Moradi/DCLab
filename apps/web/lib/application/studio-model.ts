/**
 * Model screens in plain words (V7-A4). Pure, so it runs under `npm run test:components`.
 * Everything here restates fields the API already returned; a value that was not returned is left out, never guessed.
 * No final test set value is read or formatted here.
 */
import { plainText } from "./command-search.ts";
import { familyLabel } from "./studio-runs.ts";

const own = <T>(table: Record<string, T>, key: string): T | undefined => (Object.hasOwn(table, key) ? table[key] : undefined);

/** "Run 3" when the whole run list was loaded; the short id when it was only partly loaded or the run is outside it. */
export function runRef(ordinals: ReadonlyMap<string, number>, partial: boolean, experimentId: string | null | undefined): string | null {
  if (!experimentId) return null;
  const ordinal = ordinals.get(experimentId);
  return partial || ordinal === undefined ? `Run ${experimentId.slice(0, 8)}` : `Run ${ordinal}`;
}

/** The model that a graph holds for a run: `model_version produced_by experiment`. */
export function experimentOfModel(edges: ReadonlyArray<{ from: { kind: string; id: string }; to: { kind: string; id: string }; relation: string }>, modelId: string): string | null {
  return edges.find((e) => e.relation === "produced_by" && e.from.kind === "model_version" && e.from.id === modelId && e.to.kind === "experiment")?.to.id ?? null;
}

const REF_WORDS: Record<string, string> = { champion_model: "the model in use" };

/** What a model is used as. Only the "in use" reference is named in words; any other kind is shown as given, cleaned. */
export function usedAsWords(refKinds: readonly string[] | null | undefined, inUse: boolean): string {
  const kinds = [...new Set(refKinds ?? [])];
  const words = kinds.map((k) => own(REF_WORDS, k) ?? plainText(k.replaceAll("_", " "), 40));
  if (inUse && !kinds.includes("champion_model")) words.unshift(REF_WORDS.champion_model);
  return words.length ? words.join(", ") : "Not in use";
}

const DESIGN: Record<string, string> = { time: "time-ordered", group: "grouped", random: "random" };

export type SummaryInput = {
  family: string | null | undefined; algorithm: string | null | undefined; run: string | null; data: string | null;
  designKind: string | null; folds: number | null; trained: string | null; inUse: boolean;
};

/** One sentence: what the model is, which run built it, on which data and test design, when, and whether it is in use. */
export function modelSentence(i: SummaryInput): string {
  const kind = familyLabel(i.family || i.algorithm) ?? "A model";
  const parts = [`${kind}${i.run ? `, built by ${i.run}` : ""}${i.data ? ` from ${i.data}` : ""}`];
  const design = i.designKind ? own(DESIGN, i.designKind) : undefined;
  if (i.folds && design) parts.push(`checked with ${i.folds} ${design} folds of cross-validation`);
  else if (i.folds) parts.push(`checked with ${i.folds} folds of cross-validation`);
  const first = parts.join(", ");
  const when = i.trained ? ` Trained ${i.trained}.` : "";
  return `${first}.${when} ${i.inUse ? "It is the model in use." : "It is not the model in use."}`;
}

// --- threshold chooser words --------------------------------------------------------------

const GOAL_WORDS: Record<string, string> = {
  f1: "the best balance of precision and recall (F1)", balanced_accuracy: "the best balanced accuracy", accuracy: "the best accuracy",
  precision: "the highest precision", recall: "the highest recall", specificity: "the highest share of non-cases left alone (specificity)",
  expected_cost: "the lowest expected cost",
};
export const goalWords = (goal: string): string => own(GOAL_WORDS, goal) ?? plainText(goal.replaceAll("_", " "), 40);

const CONSTRAINT_WORDS: Record<string, string> = { flagged_share: "share of rows flagged", balanced_accuracy: "balanced accuracy", f1: "F1" };
export const constraintWords = (metric: string): string => own(CONSTRAINT_WORDS, metric) ?? plainText(metric.replaceAll("_", " "), 40);

// --- text from the API that must not reintroduce a banned word ----------------------------

const B = String.raw`(?<![\w-])`;
const E = String.raw`(?![\w-])`;
const swap = (text: string, pattern: string, to: string): string =>
  text.replace(new RegExp(`${B}(?:${pattern})${E}`, "gi"), (m) => (/^[A-Z]/.test(m) ? to[0].toUpperCase() + to.slice(1) : to));

/**
 * Wording for sentences the SERVER wrote as fixed text (the optimism note, the threshold tie-break, the final label,
 * what_this_means). They may say "holdout", "out-of-fold", "operating point" or "final evaluation"; on screen the same
 * things are "final test set", "training-fold", "threshold" and "final test". Only whole words are changed, and this is
 * never applied to text that contains a person's or a file's values (column names, labels, finding messages): a column
 * called `holdout_score` must stay exactly as it is.
 */
export function plainWords(text: string): string {
  let out = text;
  out = swap(out, String.raw`hold-?out\s+rows?`, "final test rows");
  out = swap(out, String.raw`hold-?out(?:\s+(?:set|data))?`, "final test set");
  out = swap(out, String.raw`final[- ]evaluation(?=\s+(?:set|rows?))`, "final test");
  out = swap(out, "final[- ]evaluation", "final test");
  out = swap(out, "out-of-fold training predictions", "predictions made on the training folds");
  out = swap(out, "out-of-fold", "training-fold");
  out = swap(out, "an operating point", "a threshold");
  out = swap(out, "operating points", "thresholds");
  out = swap(out, "operating point", "threshold");
  return out;
}

/** Page header of a model page: never a name or an in-use badge before the model is known to belong to this project. */
export type ModelHeader = { title: string; pill: { text: string; tone: "ok" | "gray" | "warn" } };
export function modelHeader(
  input: { validId: boolean; isError: boolean; data?: { version: string; project_id?: string | null; is_champion: boolean } | null },
  projectId: string,
  nameOf: (version: string) => string,
): ModelHeader {
  if (!input.validId) return { title: "Model not found", pill: { text: "not found", tone: "warn" } };
  if (input.isError) return { title: "Model", pill: { text: "status unavailable", tone: "gray" } };
  if (!input.data) return { title: "Model", pill: { text: "loading", tone: "gray" } };
  if (input.data.project_id && input.data.project_id !== projectId) return { title: "Model", pill: { text: "other project", tone: "warn" } };
  return { title: nameOf(input.data.version), pill: input.data.is_champion ? { text: "★ in use", tone: "ok" } : { text: "not in use", tone: "gray" } };
}
