/**
 * Pipeline evidence page logic (P4.17-UI): pure, so it runs under `npm run test:components`.
 *
 * Every row comes from a field of `GET /v1/model-builds/{id}` (stages, in the order the engine returns them),
 * its `/events`, `/artifacts`, the run's decision records, `GET /v1/experiments/{id}/findings` and
 * `GET /v1/agent-runs`. Nothing is computed beyond counting. The final-holdout stage keeps its status and
 * summary but its configuration (the holdout metrics) is never turned into rows: the labelled final evaluation
 * is shown by the model card and the holdout flow, not here.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";
import { isUuid } from "./command-search.ts";
import { checkLabel, formatEvidenceValue, type FindingsLike } from "./studio-findings.ts";
import { decisionMarkers, type DecisionLike, type DecisionMarker } from "./studio-graph.ts";

type Rec = Record<string, unknown>;
const rec = (value: unknown): Rec => (typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Rec) : {});
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const str = (value: unknown): string | null => (typeof value === "string" && value ? value : null);

export type StageLike = {
  key: string; sequence: number; title: string; status: string;
  duration_ms?: number | null; rows_in?: number | null; rows_out?: number | null;
  decision_summary?: string | null; reason?: string | null;
  configuration?: Rec;
  evidence_references?: Array<{ entity_type: string; id?: string | null }>;
  generated_code?: { digest: string; spec_digest: string } | null;
};

/** The engine's typed stages in the order the API returns them (sequence, then key). Nothing is added or dropped. */
export function orderStages<T extends StageLike>(stages: T[]): T[] {
  return stages.map((s, i) => ({ s, i })).sort((a, b) => a.s.sequence - b.s.sequence || a.s.key.localeCompare(b.s.key) || a.i - b.i).map((x) => x.s);
}

export type StageTone = "ok" | "crit" | "ai" | "gray";
export function stageTone(status: string): StageTone {
  const value = status.toLowerCase();
  if (["complete", "completed", "succeeded", "success"].includes(value)) return "ok";
  if (["failed", "error"].includes(value)) return "crit";
  if (["running", "started", "in_progress"].includes(value)) return "ai";
  return "gray";
}

export function stageCounts(stages: StageLike[]): { total: number; completed: number; failed: number; running: number } {
  const tones = stages.map((s) => stageTone(s.status));
  return { total: stages.length, completed: tones.filter((t) => t === "ok").length, failed: tones.filter((t) => t === "crit").length, running: tones.filter((t) => t === "ai").length };
}

export function durationLabel(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  return rest ? `${minutes} min ${rest} s` : `${minutes} min`;
}

/** Wall-clock run duration from the experiment's own timestamps; null while it has not both started and ended. */
export function runDurationMs(startedAt: string | null | undefined, endedAt: string | null | undefined): number | null {
  if (!startedAt || !endedAt) return null;
  const ms = new Date(endedAt).getTime() - new Date(startedAt).getTime();
  return Number.isFinite(ms) && ms >= 0 ? ms : null;
}

// --- digests and results -----------------------------------------------------------------

export type Digest = { label: string; value: string };
const DIGEST_KEY = /(^|_)digest$/;
const DIGEST_VALUE = /^[A-Za-z0-9:._-]{8,200}$/;
const DIGEST_CAP = 12;
const human = (text: string) => text.replaceAll("_", " ");

function collectDigests(value: unknown, path: string[], depth: number, out: Digest[]): void {
  if (depth > 3 || out.length >= DIGEST_CAP) return;
  if (Array.isArray(value)) {
    value.slice(0, 8).forEach((item, index) => collectDigests(item, [...path.slice(0, -1), `${path[path.length - 1] ?? "item"} ${index + 1}`], depth + 1, out));
    return;
  }
  for (const [key, item] of Object.entries(rec(value))) {
    if (typeof item === "string" && DIGEST_KEY.test(key)) {
      if (DIGEST_VALUE.test(item) && out.length < DIGEST_CAP) out.push({ label: human([...path, key].join(" · ")), value: item });
    } else if (item && typeof item === "object") collectDigests(item, [...path, key], depth + 1, out);
  }
}

/** Digests the stage itself reports: its generated code and any `*digest` field of its configuration. */
export function stageDigests(stage: StageLike): Digest[] {
  const out: Digest[] = [];
  if (stage.generated_code?.digest && DIGEST_VALUE.test(stage.generated_code.digest)) out.push({ label: "generated code", value: stage.generated_code.digest });
  if (stage.generated_code?.spec_digest && DIGEST_VALUE.test(stage.generated_code.spec_digest)) out.push({ label: "code spec", value: stage.generated_code.spec_digest });
  collectDigests(stage.configuration ?? {}, [], 0, out);
  return out;
}

/** Stage whose configuration holds holdout metrics: shown as a locked note, never as rows. */
export const HOLDOUT_STAGES = new Set(["final_holdout"]);
const HIDDEN_KEY = /holdout|^test_(?!size)|_test$|y_true|y_pred|prediction|^score_delta$/i;
const RESULT_CAP = 10;
const SKIP_KEY = /(^|_)(id|ids)$|digest$/;

export type ResultRow = { key: string; label: string; value: string };
export type StageResult = { rows: ResultRow[]; more: number; locked: boolean };

export function stageResult(stage: StageLike): StageResult {
  if (HOLDOUT_STAGES.has(stage.key)) return { rows: [], more: 0, locked: true };
  const rows: ResultRow[] = [];
  for (const [key, value] of Object.entries(stage.configuration ?? {})) {
    if (HIDDEN_KEY.test(key) || SKIP_KEY.test(key) || value === null || value === undefined) continue;
    let text: string;
    if (Array.isArray(value)) text = value.every((v) => v === null || ["string", "number", "boolean"].includes(typeof v)) ? formatEvidenceValue(key, value) : `${value.length} recorded (the experiment page lists them)`;
    else if (typeof value === "object") text = `${Object.keys(value as Rec).length} fields recorded`;
    else text = formatEvidenceValue(key, value);
    const label = human(key);
    rows.push({ key, label: label.charAt(0).toUpperCase() + label.slice(1), value: text });
  }
  return { rows: rows.slice(0, RESULT_CAP), more: Math.max(0, rows.length - RESULT_CAP), locked: false };
}

// --- checks ------------------------------------------------------------------------------

export type CheckStatus = "pass" | "warn" | "fail" | "other";
export type CheckRow = { id: string; label: string; status: CheckStatus; text: string };

function checkStatus(raw: string): CheckStatus {
  const value = raw.toLowerCase();
  if (["verified", "pass", "passed", "ok"].includes(value)) return "pass";
  if (["verified_with_warnings", "warn", "warning"].includes(value)) return "warn";
  if (["failed", "fail", "error"].includes(value)) return "fail";
  return "other";
}
export const checkStatusLabel = (status: CheckStatus): string => ({ pass: "Passed", warn: "Warning", fail: "Failed", other: "Not evaluated" })[status];

/** Which stage each trust check is shown on. A check whose stage the run does not have is listed under the run's checks instead. */
export const CHECK_STAGE: Record<string, string> = {
  target_leakage: "leakage_audit", overfit_gap: "cv_training", duplicate_rows: "structural_cleaning",
  class_imbalance: "problem_profile", implausible_score: "candidate_comparison",
};

/** Verification attempts a stage recorded (overall status only: per-check holdout-scoped details stay in the verifier report). */
function attemptChecks(stage: StageLike): CheckRow[] {
  return list(stage.configuration?.attempts).map(rec).map((attempt, index) => {
    const raw = str(attempt.deterministic_status) ?? "unknown";
    return {
      id: `${stage.key}-attempt-${index}`,
      label: `Deterministic verification (${human(str(attempt.audit_mode) ?? "audit")})`,
      status: checkStatus(raw),
      text: `Overall status ${raw.toLowerCase()}. The verifier also checks the locked holdout; only this overall result is shown here.`,
    };
  });
}

export function checksByStage(stages: StageLike[], findings: FindingsLike | null | undefined): { byStage: Map<string, CheckRow[]>; loose: CheckRow[] } {
  const keys = new Set(stages.map((s) => s.key));
  const byStage = new Map<string, CheckRow[]>();
  const loose: CheckRow[] = [];
  for (const stage of stages) {
    const rows = attemptChecks(stage);
    if (rows.length) byStage.set(stage.key, rows);
  }
  for (const finding of findings?.checks ?? []) {
    const row: CheckRow = { id: `finding-${finding.check}`, label: checkLabel(finding.check), status: checkStatus(finding.status), text: finding.message };
    const target = CHECK_STAGE[finding.check];
    if (target && keys.has(target)) byStage.set(target, [...(byStage.get(target) ?? []), row]);
    else loose.push(row);
  }
  return { byStage, loose };
}

export function checkTotals(rows: CheckRow[]): { pass: number; warn: number; fail: number; other: number; total: number } {
  const count = (status: CheckStatus) => rows.filter((r) => r.status === status).length;
  return { pass: count("pass"), warn: count("warn"), fail: count("fail"), other: count("other"), total: rows.length };
}

// --- decision records and decision points ------------------------------------------------

export type RecordLike = DecisionLike & { actor: { kind: string; rule?: string | null; agent_run_id?: string | null } };

/** Same-origin Decisions link with the record selected; null for an invalid id. */
export function decisionHref(projectId: string, recordId: string): string | null {
  if (!isUuid(projectId) || !isUuid(recordId)) return null;
  return safeInternalHref(`/projects/${projectId}/decisions?record=${recordId}`);
}

/** Parse `?record=`: a lowercase UUID or nothing. */
export function parseRecordParam(value: string | null): string | null {
  return isUuid(value) ? value : null;
}

/** Engine-owned record types that belong to a stage's outcome (the stage card links them). */
const STAGE_RECORD_TYPES: Record<string, string[]> = {
  winner_lock: ["winner_locked"], holdout_lock: ["split_plan_created"], final_holdout_plan: ["split_plan_created"], target_task: ["problem_spec_locked"],
};
export function stageRecords<T extends RecordLike>(stageKey: string, records: T[]): T[] {
  const types = STAGE_RECORD_TYPES[stageKey] ?? [];
  return records.filter((r) => types.includes(r.decision_type));
}

/** Where each decision point is shown in the typed stages, and whether the AI note is before or after the stage. */
const POINT_PLACEMENT: Record<string, { stage: string; when: "before" | "after" }> = {
  "target.column": { stage: "target_task", when: "before" },
  "spec.objective": { stage: "metric_plan", when: "before" },
  "column.is_identifier": { stage: "feature_engineering", when: "before" },
  "column.semantic_role": { stage: "feature_engineering", when: "before" },
  "column.missing_value_action": { stage: "missing_value_decisions", when: "before" },
  "feature.leakage_suspect": { stage: "leakage_audit", when: "before" },
  "split.strategy": { stage: "holdout_lock", when: "before" },
  "training.families_budget": { stage: "candidate_generation", when: "before" },
  "experiment.review": { stage: "deterministic_verification", when: "after" },
};

const CROSS_CHECK = new Set(["column.is_identifier", "column.semantic_role", "feature.leakage_suspect"]);

export type EventLike = { event_type: string; stage?: string; status?: string; sequence: number; payload: Rec };

export type PointRow = {
  key: string;
  level: 0 | 1 | 2 | 3 | null;
  ai: string | null;
  reason: string | null;
  agreement: string | null;
  recordId: string | null;
  recordState: string | null;
  actor: string | null;
  answers: DecisionMarker["answers"];
  answersTotal: number | null;
  stage: string | null;
  when: "before" | "after" | null;
  /** AI never took part: the rule's answer was used and no AI answer exists. */
  aiOff: boolean;
  /** Cross-check points compare both answers rather than asking the AI first. */
  crossCheck: boolean;
};

const asLevel = (value: unknown): 0 | 1 | 2 | 3 | null => (value === 0 || value === 1 || value === 2 || value === 3 ? value : null);

/** One row per decision point: the pipeline event (AI on or off) joined to its record when one was written. */
export function decisionPointRows(events: EventLike[], records: RecordLike[]): PointRow[] {
  const markers = [...decisionMarkers(records).values()].flat();
  const markerByPoint = new Map<string, DecisionMarker>();
  for (const marker of markers) {
    if (!marker.point) continue;
    const known = markerByPoint.get(marker.point);
    const better = !known || (known.state === "superseded" && marker.state !== "superseded") || (known.state === marker.state && marker.recordedAt > known.recordedAt);
    if (better) markerByPoint.set(marker.point, marker);
  }
  const eventByPoint = new Map<string, Rec & { sequence: number }>();
  for (const event of events) {
    if (event.event_type !== "decision_point_resolved") continue;
    const point = str(event.payload.decision_point);
    if (!point) continue;
    const known = eventByPoint.get(point);
    if (!known || event.sequence > known.sequence) eventByPoint.set(point, { ...event.payload, sequence: event.sequence });
  }
  const keys = [...new Set([...eventByPoint.keys(), ...markerByPoint.keys()])].sort();
  return keys.map((key) => {
    const event = eventByPoint.get(key);
    const marker = markerByPoint.get(key);
    const place = Object.hasOwn(POINT_PLACEMENT, key) ? POINT_PLACEMENT[key] : null;
    const ai = event ? str(event.ai) : marker ? "on" : null;
    return {
      key,
      level: marker?.level ?? asLevel(event?.level),
      ai, reason: event ? str(event.reason) : null,
      agreement: marker?.agreement ?? (event ? str(event.agreement) : null),
      recordId: marker?.id ?? null, recordState: marker?.state ?? null,
      actor: marker?.actor ?? null,
      answers: marker?.answers ?? [], answersTotal: marker?.answersTotal ?? null,
      stage: place?.stage ?? null, when: place?.when ?? null,
      aiOff: ai === "off" || (!marker && ai !== "on" && ai !== "inherited"),
      crossCheck: CROSS_CHECK.has(key),
    };
  });
}

export function pointsForStage(rows: PointRow[], stageKey: string): PointRow[] {
  return rows.filter((r) => r.stage === stageKey);
}

/** The single value of one answer column when every answer agrees, else a count (the per-column table is the detail). */
export function answerSummary(row: PointRow, field: "rule" | "ai" | "used"): string {
  if (!row.answers.length) return row.aiOff ? (field === "ai" ? "AI off: no answer" : "rule's answer") : "—";
  const values = [...new Set(row.answers.map((a) => a[field]))];
  const total = Math.max(row.answersTotal ?? row.answers.length, row.answers.length);
  if (total > row.answers.length) return values.length === 1 ? `${values[0]} (first ${row.answers.length} of ${total} columns)` : `${total} columns: see the list`;
  return values.length === 1 ? values[0] : `${total} columns differ: see the list`;
}

// --- agent runs and replay ---------------------------------------------------------------

export type AgentRunLike = {
  id: string; agent_key: string; agent_version?: string; kind: string; status: string; cost_micros: number; currency: string;
  subject: { kind: string; id?: string | null }; usage?: Rec; created_at: string; decision_point_key?: string | null;
};
const PRIVATE_KINDS = new Set(["lead", "assistant"]);
const FINISHED = new Set(["completed", "failed", "cancelled", "canceled", "refused"]);

/** Specialist runs about this experiment, newest first. Lead and assistant runs are private and not replayable. */
export function runsForExperiment<T extends AgentRunLike>(runs: T[], experimentId: string): T[] {
  return runs.filter((r) => r.subject.kind === "experiment" && r.subject.id === experimentId && !PRIVATE_KINDS.has(r.kind))
    .sort((a, b) => b.created_at.localeCompare(a.created_at) || a.id.localeCompare(b.id));
}
export const canReplay = (run: AgentRunLike): boolean => FINISHED.has(run.status.toLowerCase());

export function costLabel(runs: AgentRunLike[]): { cost: string; calls: string } {
  if (!runs.length) return { cost: "none recorded", calls: "no AI runs" };
  const currencies = [...new Set(runs.map((r) => r.currency))];
  const micros = runs.reduce((sum, r) => sum + (Number.isFinite(r.cost_micros) ? r.cost_micros : 0), 0);
  const cost = currencies.length === 1 ? `${(micros / 1_000_000).toFixed(micros < 10_000 ? 4 : 2)} ${currencies[0]}` : "mixed currencies";
  return { cost, calls: `${runs.length} agent run${runs.length === 1 ? "" : "s"}` };
}

export type ReplayResult = {
  equal: boolean; same_failure?: boolean; not_comparable?: boolean; mismatches: string[]; incident_id?: string | null;
  tool_sequence: Array<{ tool: string; argument_digest: string }>; output_digest?: string | null;
};
export type ReplayView = { tone: "ok" | "warn" | "crit"; title: string; detail: string; reasons: string[]; tools: Array<{ tool: string; digest: string }> };

export function replayView(result: ReplayResult): ReplayView {
  const tools = result.tool_sequence.map((t) => ({ tool: t.tool, digest: t.argument_digest }));
  const reasons = result.mismatches.map((m) => m.slice(0, 300));
  if (result.same_failure) return { tone: "warn", title: "Same failure on replay", detail: "The recorded run failed, and the replay failed the same way. That is not a mismatch: the record is reproducible.", reasons, tools };
  if (result.not_comparable) return { tone: "warn", title: "Not comparable", detail: "The record has no final output digest, so the output could not be compared. The tool calls it made are listed.", reasons, tools };
  if (result.equal) return { tone: "ok", title: "Replay matches the record", detail: "Re-running the agent from its record gave the same tool calls, the same output digest and the same proposals.", reasons, tools };
  return {
    tone: "crit", title: "Replay does not match the record",
    detail: `The replay differed from what was recorded${result.incident_id ? "; an incident was opened for the platform team" : ""}. The differences are listed below.`, reasons, tools,
  };
}

/** Plain words for a refused or failed replay (typed API errors). */
export function replayErrorText(error: unknown): { title: string; detail: string } {
  const status = (error as { status?: unknown } | null)?.status;
  const body = (error as { body?: { error?: { code?: unknown } } } | null)?.body;
  const code = typeof body?.error?.code === "string" ? body.error.code.toLowerCase() : "";
  if (code === "not_replayable") return { title: "This run cannot be replayed", detail: "Lead and assistant runs are private and are not replayable yet. Specialist runs, such as the Critic review, are." };
  if (code === "run_not_finished") return { title: "The agent run is still running", detail: "Replay it once it has finished." };
  if (code === "runtime_unavailable") return { title: "Replay is not available right now", detail: "The replay runtime is switched off or unavailable. The recorded answer is unchanged." };
  if (status === 429 || code === "rate_limited") return { title: "Too many replays", detail: "Replay is rate limited. Wait a minute and try again." };
  if (status === 504 || code === "replay_timeout") return { title: "The replay timed out", detail: "It did not finish in time. Nothing was changed; try again later." };
  if (status === 409) return { title: "The replay was refused", detail: "The run is not in a state that can be replayed." };
  if (status === 403) return { title: "You cannot replay this", detail: "Replay needs a role that can run ML work in this workspace. The API enforces this." };
  if (status === 404) return { title: "Agent run not found", detail: "It does not exist in the active workspace, or you cannot see it." };
  return { title: "The replay did not run", detail: error instanceof Error && error.message ? error.message : "Try again." };
}

// --- artifacts and downloads -------------------------------------------------------------

/**
 * Artifact types this page offers for download (a page choice, not a security boundary: the API authorizes every
 * download). Model files, prediction files and run reports are left to the pages that label them; the rest is listed
 * with its digest only.
 */
export const DOWNLOADABLE_ARTIFACT_TYPES = new Set(["source_code", "dependency_lock", "feature_manifest"]);
export const canDownloadArtifact = (artifact: { id: string; artifact_type: string }): boolean => isUuid(artifact.id) && DOWNLOADABLE_ARTIFACT_TYPES.has(artifact.artifact_type);

export function sizeLabel(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

// --- provenance --------------------------------------------------------------------------

export type ProvenanceRow = { key: string; label: string; value: string; mono: boolean };

/** Provenance fields exactly as the API returns them; a field it does not return is not shown. */
export function provenanceRows(input: {
  build: { pipeline_run_id: string; workspace_id: string; generator_version?: string | null; reproduction_spec_digest?: string | null; scientific_evidence_locked_at?: string | null; compatibility_fallback_used?: boolean };
  stages: StageLike[]; lineage?: { source_dataset_id?: string | null; split_plan_id?: string | null; problem_spec_id?: string | null; parent_experiment_id?: string | null } | null;
  eventCount: number; formatWhen: (value: string) => string;
}): ProvenanceRow[] {
  const rows: ProvenanceRow[] = [];
  const add = (key: string, label: string, value: string | null | undefined, mono = true) => { if (value) rows.push({ key, label, value, mono }); };
  const { build, lineage } = input;
  add("run", "Model build id (the experiment id)", build.pipeline_run_id);
  add("generator", "Engine and code generator version", build.generator_version);
  add("spec", "Reproduction spec digest", build.reproduction_spec_digest);
  add("locked", "Scientific evidence locked", build.scientific_evidence_locked_at ? input.formatWhen(build.scientific_evidence_locked_at) : null, false);
  add("dataset", "Dataset version", lineage?.source_dataset_id);
  add("split", "Split plan", lineage?.split_plan_id);
  add("spec_id", "Problem spec", lineage?.problem_spec_id);
  add("parent", "Parent run", lineage?.parent_experiment_id);
  const runtime = input.stages.flatMap((s) => list(rec(s.configuration).runtime_environments)).map(rec);
  runtime.forEach((env, i) => {
    add(`env-${i}`, `Runtime environment digest${runtime.length > 1 ? ` ${i + 1}` : ""}`, str(env.environment_digest));
    const detail = [str(env.python_version) && `Python ${str(env.python_version)}`, str(env.os_name), str(env.architecture)].filter(Boolean).join(", ");
    add(`env-detail-${i}`, `Runtime environment${runtime.length > 1 ? ` ${i + 1}` : ""}`, detail, false);
  });
  const snapshots = input.stages.flatMap((s) => list(rec(s.configuration).code_snapshots)).map(rec);
  snapshots.forEach((snap, i) => add(`code-${i}`, `Code snapshot digest${snapshots.length > 1 ? ` ${i + 1}` : ""}`, str(snap.code_digest)));
  const seed = input.stages.map((s) => rec(s.configuration)).map((c) => c.seed ?? c.random_seed ?? c.random_state).find((v) => typeof v === "number" || typeof v === "string");
  add("seed", "Random seed", seed === undefined ? null : String(seed));
  add("events", "Pipeline events recorded", String(input.eventCount), false);
  if (build.compatibility_fallback_used) add("compat", "Evidence source", "Some stages were read from the older run record (compatibility fallback)", false);
  return rows;
}

/** Run switcher entries: the project's newest runs, the current one always included. */
export function switcherRuns<T extends { id: string; created_at: string }>(runs: T[], currentId: string, max = 12): T[] {
  const sorted = [...runs].sort((a, b) => b.created_at.localeCompare(a.created_at) || a.id.localeCompare(b.id));
  const head = sorted.slice(0, max);
  const current = sorted.find((r) => r.id === currentId);
  return current && !head.includes(current) ? [...head, current] : head;
}

export function pipelineHref(projectId: string, experimentId: string): string | null {
  if (!isUuid(projectId) || !isUuid(experimentId)) return null;
  return safeInternalHref(`/projects/${projectId}/pipeline/${experimentId}`);
}
