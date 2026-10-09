/**
 * Model card page logic (P4.11-UI): pure, so it runs under `npm run test:components`.
 *
 * Every value comes from `GET /v1/model-versions/{id}/card` (JSON; its `markdown` field is the download).
 * This module only orders, labels and formats. The final evaluation is shown only when the API reports it
 * (status "reported"); it is the single evaluation of the locked winner and is never merged with CV numbers.
 */
import { isUuid, plainText, projectHref } from "./command-search.ts";
import { sortFindings } from "./studio-findings.ts";
import { familyLabel } from "./studio-runs.ts";
import { metricInfo } from "./studio-goal.ts";
import { modelName } from "./studio-names.ts";
import { experimentOfModel } from "./studio-model.ts";

export const FINAL_EVAL_HEADING = "Final test (used once per run)";
export const FINAL_EVAL_SUBHEADING = "These rows were set aside before any modelling. This run scored them once, for its chosen model only. They did not choose the model, its settings or its threshold, and this is not a cross-validation score.";

export type CardDriverLike = { rank: number; column: string; importance_mean?: number | null; importance_std?: number | null; importance_se?: number | null; distinguishable?: boolean | null };
export type CardDriversLike = { status: string; text: string; features?: CardDriverLike[]; clear_drivers?: string[]; columns_tested?: number | null; folds?: number | null };
export type CardRiskLike = { check: string; status: string; severity: string; message: string };
export type CardFinalLike = { status: string; label?: string | null; metric?: string | null; value?: number | null; metrics?: Record<string, number>; decision_threshold?: number | null; note?: string | null };

const TITLE_MAX = 80;

export function cardTitle(card: { family?: string | null; algorithm?: string | null; version: string }): string {
  const kind = familyLabel(card.family || card.algorithm) ?? "model";
  return `Model card: ${plainText(kind, TITLE_MAX)} · ${modelName(plainText(card.version, 40))}`;
}

/** `model-card-<first 8 of the model version id>.md`; null when the id is not a UUID. */
export function cardFilename(modelVersionId: string | null | undefined): string | null {
  return isUuid(modelVersionId) ? `model-card-${modelVersionId.slice(0, 8).toLowerCase()}.md` : null;
}

/** The API's Markdown, saved as plain text. Null when the card carries none. */
export function markdownDownload(card: { markdown?: string | null }): { text: string; type: string } | null {
  const text = typeof card.markdown === "string" ? card.markdown : "";
  return text.trim() ? { text, type: "text/markdown;charset=utf-8" } : null;
}

export function metricName(name: string | null | undefined): string {
  return name ? (metricInfo(name)?.label ?? plainText(name.replaceAll("_", " "), 60)) : "score";
}

/** Compact number: integers as is, ratios with 3 significant digits. */
export function cardNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "n/a";
  if (Number.isInteger(value)) return value.toLocaleString("en-US");
  return Math.abs(value) >= 100 ? value.toFixed(1) : Number(value.toPrecision(3)).toString();
}

// --- drivers -------------------------------------------------------------------------------

export type DriverBar = { rank: number; column: string; importance: string; width: number; clear: boolean | null; spread: string | null };
export type DriversView =
  | { state: "bars"; text: string; bars: DriverBar[]; clearCount: number; clearTotal: number | null; maxShown: number }
  | { state: "empty"; text: string; reason: string };

const DRIVER_EMPTY: Record<string, string> = {
  not_computed: "Importance was not computed for this run. Runs finished before the model card existed carry no stored importance.",
  skipped: "Importance was skipped for this run.",
  not_applicable: "Importance does not apply to this model.",
};

/** Bars from stored importance only (never recomputed). Width is relative to the largest positive importance. */
export function driversView(drivers: CardDriversLike): DriversView {
  const features = [...(drivers.features ?? [])].sort((a, b) => a.rank - b.rank);
  if (drivers.status !== "computed" || features.length === 0) {
    return { state: "empty", text: plainText(drivers.text, 600), reason: DRIVER_EMPTY[drivers.status] ?? "No importance is stored for this model." };
  }
  const max = Math.max(0, ...features.map((f) => (typeof f.importance_mean === "number" && Number.isFinite(f.importance_mean) ? f.importance_mean : 0)));
  const bars = features.map((f) => {
    const mean = typeof f.importance_mean === "number" && Number.isFinite(f.importance_mean) ? f.importance_mean : null;
    return {
      rank: f.rank,
      column: plainText(f.column, 80),
      importance: cardNumber(mean),
      width: mean !== null && mean > 0 && max > 0 ? Math.max(2, Math.round((mean / max) * 100)) : 0,
      clear: f.distinguishable ?? null,
      spread: typeof f.importance_std === "number" && Number.isFinite(f.importance_std) ? `± ${cardNumber(f.importance_std)}` : null,
    };
  });
  return { state: "bars", text: plainText(drivers.text, 600), bars, clearCount: bars.filter((b) => b.clear).length, clearTotal: drivers.clear_drivers?.length ?? null, maxShown: bars.length };
}

// --- baseline ------------------------------------------------------------------------------

export type BaselineView = { tone: "ok" | "warn" | "gray"; badge: string; text: string; rows: Array<{ key: string; label: string; value: string }> };

export function baselineView(b: { available: boolean; text: string; metric?: string | null; baseline_score?: number | null; winner_score?: number | null; margin?: number | null; beats_baseline?: boolean | null; clear_margin?: boolean | null }): BaselineView {
  const text = plainText(b.text, 600);
  if (!b.available) return { tone: "gray", badge: "No baseline available", text, rows: [] };
  const rows = [
    { key: "winner", label: `Chosen model (${metricName(b.metric)}, cross-validation)`, value: cardNumber(b.winner_score) },
    { key: "baseline", label: `Dummy baseline (${metricName(b.metric)}, cross-validation)`, value: cardNumber(b.baseline_score) },
    { key: "margin", label: "Gain over the baseline (cross-validation)", value: cardNumber(b.margin) },
  ];
  if (b.beats_baseline === true && b.clear_margin !== false) return { tone: "ok", badge: "Clearly beats the baseline", text, rows };
  if (b.beats_baseline === true) return { tone: "warn", badge: "Beats the baseline by a small margin", text, rows };
  if (b.beats_baseline === false) return { tone: "warn", badge: "Does not beat the baseline", text, rows };
  return { tone: "gray", badge: "Baseline comparison unknown", text, rows };
}

// --- final evaluation ----------------------------------------------------------------------

export type FinalView =
  | { state: "reported"; metric: string; value: string; others: Array<{ key: string; value: string }>; threshold: string | null; note: string | null }
  | { state: "withheld" | "missing"; text: string };

/** The labelled single evaluation. Renders numbers only when the API says "reported" and sends a value. */
export function finalView(final: CardFinalLike): FinalView {
  if (final.status === "withheld") return { state: "withheld", text: "Withheld: this view of the card is for connected tools and the assistant, which never see final test values." };
  const metrics = Object.entries(final.metrics ?? {}).filter((e): e is [string, number] => typeof e[1] === "number" && Number.isFinite(e[1]));
  const hasValue = typeof final.value === "number" && Number.isFinite(final.value);
  if (final.status !== "reported" || (!hasValue && metrics.length === 0)) {
    return { state: "missing", text: plainText(final.note, 300) || "The API did not report a final evaluation for this model version." };
  }
  const primary = final.metric ?? null;
  return {
    state: "reported",
    metric: metricName(primary),
    value: cardNumber(hasValue ? final.value : metrics.find(([k]) => k === primary)?.[1] ?? null),
    others: metrics.filter(([k]) => k !== primary && k !== "decision_threshold").sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => ({ key: metricName(k), value: cardNumber(v) })),
    threshold: typeof final.decision_threshold === "number" ? cardNumber(final.decision_threshold) : null,
    note: final.note ? plainText(final.note, 300) : null,
  };
}

// --- risks ---------------------------------------------------------------------------------

export type RisksView = { state: "none_run" | "clean" | "attention"; text: string; items: Array<CardRiskLike & { message: string }>; attention: number };

export function risksView(risks: { investigated: boolean; text: string; items?: CardRiskLike[] }): RisksView {
  const text = plainText(risks.text, 600);
  if (!risks.investigated) return { state: "none_run", text, items: [], attention: 0 };
  const items = sortFindings(risks.items ?? []).map((i) => ({ ...i, message: plainText(i.message, 400) }));
  const attention = items.filter((i) => i.status === "fail" || i.status === "warning").length;
  return { state: attention ? "attention" : "clean", text, items, attention };
}

/** Link to the experiment's Findings card; null unless both ids are UUIDs. */
export function findingsHref(projectId: string, experimentId: string): string | null {
  const base = projectHref(projectId, "experiments", experimentId);
  return base ? `${base}#findings` : null;
}

// --- data and split (counts only) -------------------------------------------------------------

export type FactRow = { key: string; label: string; value: string };

export function dataRows(data: { name?: string | null; row_count?: number | null; column_count?: number | null; modeled_feature_count?: number | null }): FactRow[] {
  const rows: FactRow[] = [];
  if (data.name) rows.push({ key: "name", label: "Data file", value: plainText(data.name, 120) });
  if (data.row_count != null) rows.push({ key: "rows", label: "Rows", value: cardNumber(data.row_count) });
  if (data.column_count != null) rows.push({ key: "cols", label: "Columns", value: cardNumber(data.column_count) });
  if (data.modeled_feature_count != null) rows.push({ key: "feat", label: "Columns the model uses", value: cardNumber(data.modeled_feature_count) });
  return rows;
}

export function splitRows(split: { evaluation_split_strategy?: string | null; evaluation_fraction?: number | null; validation_strategy?: string | null; validation_folds?: number | null; train_rows?: number | null; evaluation_rows?: number | null; stratified?: boolean | null; group_column?: string | null; time_column?: string | null }): FactRow[] {
  const rows: FactRow[] = [];
  if (split.train_rows != null) rows.push({ key: "train", label: "Training rows", value: cardNumber(split.train_rows) });
  if (split.evaluation_rows != null) rows.push({ key: "eval", label: "Final test set rows (count only)", value: cardNumber(split.evaluation_rows) });
  if (split.evaluation_split_strategy) rows.push({ key: "strategy", label: "How the final test set was set aside", value: plainText(split.evaluation_split_strategy.replaceAll("_", " "), 60) });
  if (split.evaluation_fraction != null) rows.push({ key: "fraction", label: "Final test set share of rows", value: `${(split.evaluation_fraction * 100).toFixed(1)}%` });
  if (split.validation_strategy) rows.push({ key: "validation", label: "Cross-validation", value: `${plainText(split.validation_strategy.replaceAll("_", " "), 60)}${split.validation_folds ? `, ${split.validation_folds} folds` : ""}` });
  if (split.stratified != null) rows.push({ key: "strat", label: "Same outcome mix in every part (stratified)", value: split.stratified ? "yes" : "no" });
  if (split.group_column) rows.push({ key: "group", label: "Group column", value: plainText(split.group_column, 80) });
  if (split.time_column) rows.push({ key: "time", label: "Time column", value: plainText(split.time_column, 80) });
  return rows;
}

/** Wording for where the threshold came from; never claims tuning when the API says it is the default. */
export function thresholdSourceText(source: string | null | undefined): string {
  if (source === "default") return "(default, not tuned)";
  if (!source) return "(locked before the final evaluation)";
  return "(chosen on cross-validation)";
}

export function llmLine(llm: { used: boolean; purposes?: string[] }): string {
  const why = llm.purposes?.length ? ` (${llm.purposes.map((p) => plainText(p.replaceAll("_", " "), 40)).join(", ")})` : "";
  return `AI used: ${llm.used ? "yes" : "no"}${why}`;
}

// --- models list --------------------------------------------------------------------------

export type ModelNodeLike = { kind: string; id: string; label: string; version?: string | null; digest?: string | null; created_at?: string | null; ref_kinds?: string[] };
export type ModelRow = { id: string; label: string; version: string; champion: boolean; created: string | null; digest: string | null; href: string | null; experimentId: string | null };

/** Model versions of the project graph: champion first, then newest first. */
export function modelRows(projectId: string, nodes: ModelNodeLike[], edges: ReadonlyArray<{ from: { kind: string; id: string }; to: { kind: string; id: string }; relation: string }> = []): ModelRow[] {
  return nodes
    .filter((n) => n.kind === "model_version" && isUuid(n.id))
    .map((n) => ({
      id: n.id, label: plainText(n.label, 80), version: plainText(n.version ?? "", 40), champion: (n.ref_kinds ?? []).includes("champion_model"),
      created: n.created_at ?? null, digest: n.digest ?? null, href: projectHref(projectId, "models", n.id), experimentId: experimentOfModel(edges, n.id),
    }))
    .sort((a, b) => Number(b.champion) - Number(a.champion) || String(b.created ?? "").localeCompare(String(a.created ?? "")));
}
