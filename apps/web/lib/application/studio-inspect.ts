/**
 * Node inspector logic (P4.3-A), pure so it runs under `npm run test:components`:
 * which experiment backs a split plan / feature recipe node, and how the model-build
 * stages, the model card and the proposals read into table rows. Values come from API
 * fields only; nothing is computed beyond means of CV fold metrics the API returned.
 * Holdout values are never read here: the evaluation stages and `final_evaluation` are ignored.
 */
import { isUuid } from "./command-search.ts";
import type { GraphEdgeLike } from "./studio-data.ts";

export type StageLike = {
  key: string;
  reason?: string | null;
  decision_summary?: string | null;
  rows_in?: number | null;
  rows_out?: number | null;
  configuration: Record<string, unknown>;
};
export type BuildLike = { stages: StageLike[] };
type Rec = Record<string, unknown>;

const rec = (value: unknown): Rec => (typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Rec) : {});
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const str = (value: unknown): string | null => (typeof value === "string" && value ? value : typeof value === "number" ? String(value) : null);
const num = (value: unknown): number | null => (typeof value === "number" && Number.isFinite(value) ? value : null);
const stage = (build: BuildLike | undefined, key: string): StageLike | undefined => build?.stages.find((s) => s.key === key);

/** The recorded configuration of one model-build stage (empty when the stage is missing). */
export const stageConfig = (build: BuildLike | undefined, key: string): Record<string, unknown> => rec(stage(build, key)?.configuration);

// --- graph helpers --------------------------------------------------------------------

type NodeLike = { kind: string; id: string; created_at?: string | null; outside_window?: boolean };

/**
 * The newest loaded experiment behind a split plan (`experiment uses_split_plan plan`) or a feature recipe
 * (`recipe produced_by experiment`); neither node has a read of its own, so they are read through that run.
 */
export function experimentUsing(nodes: NodeLike[], edges: GraphEdgeLike[], kind: "split_plan" | "feature_recipe", nodeId: string): string | null {
  const byId = new Map(nodes.filter((n) => n.kind === "experiment").map((n) => [n.id, n]));
  const ids = edges.flatMap((e) => {
    if (kind === "split_plan") return e.relation === "uses_split_plan" && e.from.kind === "experiment" && e.to.id === nodeId ? [e.from.id] : [];
    return e.relation === "produced_by" && e.from.kind === "feature_recipe" && e.from.id === nodeId && e.to.kind === "experiment" ? [e.to.id] : [];
  });
  const hits = ids.filter((id) => byId.has(id)).map((id) => byId.get(id)!)
    .sort((a, b) => Number(Boolean(a.outside_window)) - Number(Boolean(b.outside_window)) || (b.created_at ?? "").localeCompare(a.created_at ?? "") || a.id.localeCompare(b.id));
  return hits[0]?.id ?? null;
}

/** The model version a run produced (`produced_by` edge), if any. */
export function modelVersionOf(edges: GraphEdgeLike[], experimentId: string): string | null {
  return edges.find((e) => e.relation === "produced_by" && e.from.kind === "model_version" && e.to.id === experimentId)?.from.id ?? null;
}

/** Inspector route of a node (UUID-checked); null when the kind has no inspector. */
export function inspectorPath(projectId: string, kind: string, id: string): string | null {
  const section: Record<string, string> = { experiment: "experiments", dataset_version: "data", split_plan: "splits", feature_recipe: "features", model_version: "models" };
  if (!isUuid(projectId) || !isUuid(id) || !Object.hasOwn(section, kind)) return null;
  return `/projects/${projectId}/${section[kind]}/${id}`;
}

// --- candidates, folds ----------------------------------------------------------------

export type CandidateRow = {
  id: string; algorithm: string; family: string; status: string; fingerprint: string;
  hyperparameters: Array<[string, string]>; folds: number; cv: Record<string, number>; selected: boolean; runnerUp: boolean;
};

export function mean(values: number[]): number | null {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

export type FoldRow = { id: string; candidate: string; fold: number; trainRows: number | null; validationRows: number | null; metrics: Record<string, number> };

export function foldRows(build: BuildLike | undefined): FoldRow[] {
  const names = new Map(candidateBase(build).map((c) => [c.id, c.algorithm]));
  return list(rec(stage(build, "cv_training")?.configuration).folds).map(rec).map((f, i) => ({
    id: str(f.id) ?? `fold-${i}`,
    candidate: names.get(str(f.candidate_id) ?? "") ?? (str(f.candidate_id) ?? "—").slice(0, 8),
    fold: num(f.fold_number) ?? i,
    trainRows: num(f.train_row_count),
    validationRows: num(f.validation_row_count),
    metrics: Object.fromEntries(Object.entries(rec(f.metrics)).filter((e): e is [string, number] => typeof e[1] === "number")),
  })).sort((a, b) => a.candidate.localeCompare(b.candidate) || a.fold - b.fold);
}

function candidateBase(build: BuildLike | undefined) {
  return list(rec(stage(build, "candidate_generation")?.configuration).candidates).map(rec).map((c) => ({
    id: str(c.id) ?? "",
    algorithm: str(c.algorithm) ?? str(c.model_family) ?? "—",
    family: str(c.model_family) ?? "—",
    status: str(c.status) ?? "—",
    fingerprint: str(c.fingerprint) ?? "—",
    hyperparameters: Object.entries(rec(c.hyperparameters)).map(([k, v]): [string, string] => [k, typeof v === "string" ? v : JSON.stringify(v)]),
  })).filter((c) => c.id);
}

/** Candidates with their CV means (from the aggregate CV evaluations, else the mean of fold metrics). CV only. */
export function candidateRows(build: BuildLike | undefined): CandidateRow[] {
  const lock = rec(stage(build, "winner_lock")?.configuration);
  const scores = new Map(list(rec(stage(build, "candidate_comparison")?.configuration).candidate_scores).map(rec).map((s) => [str(s.candidate_id) ?? "", rec(s.metrics)]));
  const folds = list(rec(stage(build, "cv_training")?.configuration).folds).map(rec);
  return candidateBase(build).map((c) => {
    const own = folds.filter((f) => str(f.candidate_id) === c.id);
    const aggregate = Object.fromEntries(Object.entries(scores.get(c.id) ?? {}).filter((e): e is [string, number] => typeof e[1] === "number"));
    const names = new Set(own.flatMap((f) => Object.keys(rec(f.metrics))));
    const fromFolds: Record<string, number> = {};
    for (const name of names) {
      const m = mean(own.map((f) => num(rec(f.metrics)[name])).filter((v): v is number => v !== null));
      if (m !== null) fromFolds[name] = m;
    }
    return { ...c, folds: own.length, cv: Object.keys(aggregate).length ? aggregate : fromFolds, selected: str(lock.selected_candidate_id) === c.id, runnerUp: str(lock.runner_up_candidate_id) === c.id };
  }).sort((a, b) => Number(b.selected) - Number(a.selected) || Number(b.runnerUp) - Number(a.runnerUp) || a.algorithm.localeCompare(b.algorithm));
}

/** Metric names to chart/show first: the run's selection metric when it is among them. */
export function metricNames(rows: Array<{ cv?: Record<string, number>; metrics?: Record<string, number> }>, preferred?: string | null): string[] {
  const all = [...new Set(rows.flatMap((r) => Object.keys(r.cv ?? r.metrics ?? {})))].sort();
  return preferred && all.includes(preferred) ? [preferred, ...all.filter((n) => n !== preferred)] : all;
}

// --- features, split ------------------------------------------------------------------

export type FeatureRow = {
  id: string; name: string; kind: string; origin: string; decision: string; formula: string | null;
  transforms: Array<{ sequence: number; type: string; transformer: string | null }>; sources: string[];
};

export function featureRows(build: BuildLike | undefined): FeatureRow[] {
  return list(rec(stage(build, "feature_engineering")?.configuration).features).map(rec).map((f, i) => ({
    id: str(f.id) ?? `feature-${i}`,
    name: str(f.name) ?? "—",
    kind: str(f.feature_type) ?? "—",
    origin: str(f.origin) ?? "—",
    decision: str(f.decision) ?? "—",
    formula: str(f.definition),
    transforms: list(f.transformations).map(rec).map((t) => ({ sequence: num(t.sequence) ?? 0, type: str(t.transformation_type) ?? "—", transformer: str(t.transformer_class) })),
    sources: list(f.sources).map(rec).map((s) => str(s.column_name)).filter((s): s is string => s !== null),
  }));
}

/** Why the recipe is what it is: the stage's recorded reason, summary and the data-prep decision counts. */
export function featureReason(build: BuildLike | undefined): string | null {
  const fe = stage(build, "feature_engineering");
  return fe?.reason ?? fe?.decision_summary ?? null;
}

export type PrepStep = { sequence: number; scope: string; type: string; transformer: string; fit: string };

/** Fitted preprocessing steps of the run (`preprocessing` stage); each is fitted on the training part of a fold only. */
export function preprocessingSteps(build: BuildLike | undefined): PrepStep[] {
  return list(rec(stage(build, "preprocessing")?.configuration).steps).map(rec).map((r, i) => ({
    sequence: num(r.sequence) ?? i, scope: str(r.column_scope) ?? "—", type: str(r.transformer_type) ?? "—", transformer: str(r.transformer_class) ?? "—", fit: str(r.fit_scope) ?? "—",
  })).sort((a, b) => a.sequence - b.sequence);
}

/** Steps that apply to a feature: its own recorded transforms, else the pipeline of its column kind. Class names only. */
export function stepsFor(feature: FeatureRow, steps: PrepStep[]): string[] {
  if (feature.transforms.length) return feature.transforms.map((t) => t.transformer ?? t.type);
  const prefix = feature.kind === "numeric" ? "numer" : feature.kind === "categorical" ? "categ" : null;
  const seen = new Set<string>();
  return steps.filter((s) => prefix !== null && s.scope.toLowerCase().startsWith(prefix)).map((s) => s.transformer).filter((t) => (seen.has(t) ? false : (seen.add(t), true)));
}

/** A short, copyable snippet from the recorded class names (no invented parameters). */
export function transformSnippet(feature: FeatureRow, steps: PrepStep[]): string {
  const classes = stepsFor(feature, steps);
  const inputs = feature.sources.length ? feature.sources : [feature.name];
  const lines = classes.map((c, i) => `    ("step_${i + 1}", ${c.split(".").pop()}()),  # ${c}`);
  return [
    `# ${feature.name}: ${feature.decision}. Fitted inside each CV fold, never on the final holdout.`,
    `inputs = [${inputs.map((s) => JSON.stringify(s)).join(", ")}]`,
    classes.length ? `pipeline = Pipeline([\n${lines.join("\n")}\n])` : "# no transform is recorded: the column passes through unchanged",
  ].join("\n");
}

export type SplitFacts = {
  strategy: string | null; testSize: number | null; groupColumn: string | null; timeColumn: string | null;
  locked: boolean; lockedAt: string | null; reason: string | null;
};

export function splitFacts(build: BuildLike | undefined): SplitFacts {
  const plan = rec(stage(build, "final_holdout_plan")?.configuration);
  const lock = rec(stage(build, "holdout_lock")?.configuration);
  const merged = { ...plan, ...lock };
  return {
    strategy: str(merged.strategy), testSize: num(merged.test_size), groupColumn: str(merged.group_column), timeColumn: str(merged.time_column),
    locked: Boolean(lock.locked), lockedAt: str(lock.locked_at),
    reason: stage(build, "holdout_lock")?.reason ?? stage(build, "final_holdout_plan")?.reason ?? stage(build, "holdout_lock")?.decision_summary ?? null,
  };
}

/** Fold sizes of the selected winner (or the first candidate): counts only, never holdout rows. */
export function foldSizes(build: BuildLike | undefined): Array<{ fold: number; trainRows: number | null; validationRows: number | null }> {
  const rows = foldRows(build);
  const first = rows[0]?.candidate;
  return rows.filter((r) => r.candidate === first).map(({ fold, trainRows, validationRows }) => ({ fold, trainRows, validationRows }));
}

// --- code, files ----------------------------------------------------------------------

/** A download filename that is a plain basename (no path, no control characters). */
export function safeFilename(name: string | null | undefined, fallback: string): string {
  const base = (name ?? "").split(/[\\/]/).pop() ?? "";
  const clean = base.replace(/[^\w.\- ]+/g, "_").replace(/^\.+/, "").slice(0, 100).trim();
  return clean || fallback;
}

// --- proposals ------------------------------------------------------------------------

export type ProposalLike = {
  id: string; proposal_type: string; status: string; level_at_proposal: number; proposed_by: string; created_at: string;
  subject: { kind: string; id?: string | null }; payload: Record<string, unknown>; rule_answer?: Record<string, unknown> | null;
  proposed_rationale?: string | null; validator_verdict: string;
};

export type Level = 0 | 1 | 2 | 3;
const asLevel = (n: number): Level | null => (n === 0 || n === 1 || n === 2 || n === 3 ? n : null);

/** Proposals about one subject of one type, newest first. */
export function proposalsFor<T extends ProposalLike>(items: T[], type: string, subjectKind: string, subjectId: string): T[] {
  return items.filter((p) => p.proposal_type === type && p.subject.kind === subjectKind && p.subject.id === subjectId)
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || a.id.localeCompare(b.id));
}

export type ReviewView = {
  verdict: string | null; summary: string | null; confidence: number | null; level: Level | null; status: string; by: string;
  metrics: Array<{ metric: string; value: number }>; findings: Array<{ check: string; status: string; note: string }>;
  ruleAnswer: string | null;
};

/** The Critic's `ExperimentReviewProposal` payload as typed fields; the rule's answer is shown beside it when recorded. */
export function reviewView(p: ProposalLike): ReviewView {
  const payload = p.payload;
  return {
    verdict: str(payload.verdict), summary: str(payload.summary), confidence: num(payload.confidence), level: asLevel(p.level_at_proposal), status: p.status, by: p.proposed_by,
    metrics: list(payload.cv_metrics).map(rec).flatMap((m) => (str(m.metric) && num(m.value) !== null ? [{ metric: str(m.metric)!, value: num(m.value)! }] : [])),
    findings: list(payload.findings).map(rec).map((f) => ({ check: str(f.check) ?? "—", status: str(f.status) ?? "—", note: str(f.note) ?? "" })),
    ruleAnswer: p.rule_answer && Object.keys(p.rule_answer).length ? JSON.stringify(p.rule_answer) : null,
  };
}

export type InvestigationView = {
  level: Level | null; status: string; confidence: number | null; ruleAnswer: string | null;
  targets: Array<{ column: string; rank: number; reason: string }>;
  missing: Array<{ column: string; action: string; reason: string }>;
  leakage: Array<{ column: string; explanation: string }>; questions: string[];
};

export function investigationView(p: ProposalLike): InvestigationView {
  const payload = p.payload;
  return {
    level: asLevel(p.level_at_proposal), status: p.status, confidence: num(payload.confidence),
    ruleAnswer: p.rule_answer && Object.keys(p.rule_answer).length ? JSON.stringify(p.rule_answer) : null,
    targets: list(payload.target_candidates).map(rec).map((t) => ({ column: str(t.column) ?? "—", rank: num(t.rank) ?? 0, reason: str(t.reason) ?? "" })).sort((a, b) => a.rank - b.rank),
    missing: list(payload.missing_values).map(rec).map((m) => ({ column: str(m.column) ?? "—", action: str(m.action) ?? "—", reason: str(m.reason) ?? "" })),
    leakage: list(payload.leakage_suspects).map(rec).map((l) => ({ column: str(l.column) ?? "—", explanation: str(l.explanation) ?? "" })),
    questions: list(payload.questions).filter((q): q is string => typeof q === "string" && q.length > 0),
  };
}

export function formatNumber(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (Number.isInteger(value)) return String(value);
  const abs = Math.abs(value);
  return abs >= 100 ? value.toFixed(1) : abs >= 1 ? value.toFixed(3) : value.toFixed(4);
}
