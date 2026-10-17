/**
 * Data page logic (P4.1-C), pure so it runs under `npm run test:components`:
 * which dataset version is shown, what uses each version (from the project graph),
 * and how a profile column reads (rule role vs role used, percentages).
 */

export type GraphNodeRefLike = { kind: string; id: string };
export type GraphEdgeLike = { from: GraphNodeRefLike; to: GraphNodeRefLike; relation: string; attribute?: boolean };
export type UsedBy = { split_plan: number; experiment: number; model_version: number };

/** Nodes that use a dataset version: split plans that partition it, runs and models that train on it. */
export function usedBy(edges: GraphEdgeLike[], datasetId: string): UsedBy {
  const seen = new Set<string>();
  const out: UsedBy = { split_plan: 0, experiment: 0, model_version: 0 };
  for (const edge of edges) {
    if (edge.attribute || edge.to.kind !== "dataset_version" || edge.to.id !== datasetId) continue;
    if (edge.relation !== "uses_dataset" && edge.relation !== "partitions") continue;
    const kind = edge.from.kind as keyof UsedBy;
    if (!(kind in out) || seen.has(`${kind}:${edge.from.id}`)) continue;
    seen.add(`${kind}:${edge.from.id}`);
    out[kind] += 1;
  }
  return out;
}

/** Per-run prepared tables (targets of `prepared_as` attribute edges): versions, but not uploads. */
export function preparedIds(edges: GraphEdgeLike[]): Set<string> {
  return new Set(edges.filter((edge) => edge.relation === "prepared_as" && edge.to.kind === "dataset_version").map((edge) => edge.to.id));
}

export function usedByText(used: UsedBy): string {
  const parts = [
    used.split_plan ? `${used.split_plan} test design${used.split_plan === 1 ? "" : "s"}` : "",
    used.experiment ? `${used.experiment} run${used.experiment === 1 ? "" : "s"}` : "",
    used.model_version ? `${used.model_version} model${used.model_version === 1 ? "" : "s"}` : "",
  ].filter(Boolean);
  return parts.length ? parts.join(" · ") : "Not used yet";
}

/** The version to show: the one in the URL, else the project's dataset ref, else the newest. */
export function pickDatasetId(ids: string[], requested: string | null | undefined, currentRef: string | null | undefined): string | null {
  if (requested && ids.includes(requested)) return requested;
  if (currentRef && ids.includes(currentRef)) return currentRef;
  return ids[0] ?? null;
}

export function percent(fraction: number | null | undefined): string {
  if (fraction === null || fraction === undefined || !Number.isFinite(fraction)) return "—";
  const value = fraction * 100;
  return `${value === 0 || value >= 10 ? value.toFixed(0) : value.toFixed(1)}%`;
}

/** `ignored_free_text` → `ignored (free text)`; null → em dash. */
export function roleLabel(role: string | null | undefined): string {
  if (!role) return "—";
  return role === "ignored_free_text" ? "ignored (free text)" : role.replaceAll("_", " ");
}

export type RoleComparison = "same" | "differs" | "no_run" | "no_rule";

/** Whether the run used the rule's role; a difference names who changed it (`role_source`). */
export function compareRoles(rule: string | null | undefined, used: string | null | undefined): RoleComparison {
  if (!used) return "no_run";
  if (!rule) return "no_rule";
  return rule === used ? "same" : "differs";
}

// --- V7-A2: the file summary, why a column is used or not, and the data checks in plain words ---

export type ProfileColumnLike = {
  name: string; physical_dtype: string; rule_role?: string | null; role_used?: string | null; role_source?: string | null; missing_fraction?: number | null;
  unique_count?: number | null; leakage_excluded: boolean; leakage_risk?: string | null;
};
export type ColumnUse = { used: "yes" | "no" | "target" | "unknown"; reason: string | null };

const PREDICTOR_WORDS: Record<string, string> = { numerical: "A number the model can use", categorical: "A category the model can use", datetime: "A date the model can use", boolean: "A yes/no value the model can use" };

/**
 * Whether a column is a predictor and why, from the role the run used and the leakage plan. Nothing is guessed:
 * without a finished run (`hasRun` false) only the target and ID columns are stated, because the rule role ignores the leakage plan.
 */
export function columnUse(c: ProfileColumnLike, hasRun = true): ColumnUse {
  const role = c.role_used ?? c.rule_role ?? null;
  if (role === "target") return { used: "target", reason: "This is what we predict" };
  if (role === "identifier") return { used: "no", reason: "ID column, not a predictor" };
  if (!hasRun) return { used: "unknown", reason: "Decided when the first run starts" };
  if (c.leakage_excluded) {
    return { used: "no", reason: c.leakage_risk === "none" ? "Left out by the plan: not a predictor" : "Left out by the leakage check: it may give away the answer" };
  }
  if (!role) return { used: "unknown", reason: null };
  if (role === "ignored_free_text") {
    return { used: "no", reason: c.role_source === "branch_change_set" ? "Left out by a change you made" : "Free text, or too empty or constant to use" };
  }
  if (c.unique_count === 1) return { used: "unknown", reason: "Only one distinct value (blanks not counted), so it adds nothing" };
  return { used: "yes", reason: Object.hasOwn(PREDICTOR_WORDS, role) ? PREDICTOR_WORDS[role] : null };
}

/** Columns the leakage plan left out because they may give away the answer (ID, entity and group columns are not leakage). */
export function leakageExcluded<T extends ProfileColumnLike>(columns: T[]): T[] {
  return columns.filter((c) => c.leakage_excluded && c.role_used !== "identifier" && c.rule_role !== "identifier" && c.leakage_risk !== "none");
}

/** A column type in plain words: the role when known (number, category, date), else from the stored type name. */
export function typeLabel(dtype: string, role?: string | null): string {
  if (role === "numerical") return "number";
  if (role === "categorical") return "category";
  if (role === "datetime") return "date";
  if (role === "boolean") return "yes / no";
  const d = dtype.toLowerCase();
  if (/bool/.test(d)) return "yes / no";
  if (/date|time/.test(d)) return "date";
  if (/int|float|double|decimal|number/.test(d)) return "number";
  if (/object|str|text|categor/.test(d)) return "text";
  return dtype;
}

export type SummaryItem = { key: string; label: string; value: string; hint?: string };
type ProfileLike = {
  statistics_status: string; split_plan: { training_row_count: number; target_column: string } | null; experiment: unknown | null; columns: ProfileColumnLike[];
  entity_column?: string | null; entity_count?: number | null; time_range?: { column: string; start: string; end: string } | null; target_rate?: number | null;
};

const MONTH = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

/** "Jan 2025 – Mar 2026" (UTC months); days when the span is under two months; null when a date is not valid. */
export function periodText(start: string | null | undefined, end: string | null | undefined): string | null {
  const a = new Date(start ?? "");
  const b = new Date(end ?? "");
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime()) || b < a) return null;
  const fmt = b.getTime() - a.getTime() < 60 * 86_400_000 ? DAY : MONTH;
  return fmt.format(a) === fmt.format(b) ? fmt.format(a) : `${fmt.format(a)} – ${fmt.format(b)}`;
}

/**
 * The strip above the tabs. Rows and columns come from the file; the rest from the profile, only when it was computed over
 * the training rows. Entity count, time span and the positive-answer share are TRAINING-period values and say so; the
 * final test set is never counted (the API never returns its rows).
 */
export function fileSummary(
  dataset: { row_count: number; column_count: number }, profile: ProfileLike | null | undefined,
  clean: (v: string) => string = (v) => v, unit: string | null = null,
): SummaryItem[] {
  const computed = profile?.statistics_status === "computed" && profile.split_plan ? profile : null;
  const uses = profile?.experiment ? profile.columns.map((c) => columnUse(c, true)) : null;
  const out: SummaryItem[] = [
    {
      key: "rows", label: unit ? `rows (one per ${clean(unit)})` : "rows", value: dataset.row_count.toLocaleString("en-GB"),
      ...(computed?.split_plan ? { hint: `${computed.split_plan.training_row_count.toLocaleString("en-GB")} in the training period` } : {}),
    },
    {
      key: "cols", value: String(dataset.column_count),
      label: uses ? `columns · ${uses.filter((u) => u.used === "yes").length} used, ${uses.filter((u) => u.used === "no").length} left out` : "columns",
    },
  ];
  if (!computed) return out;
  const period = computed.time_range ? periodText(computed.time_range.start, computed.time_range.end) : null;
  if (computed.entity_column && typeof computed.entity_count === "number") {
    out.push({ key: "entity", value: computed.entity_count.toLocaleString("en-GB"), label: `different ${clean(computed.entity_column)} values · training period${period ? `, ${period}` : ""}` });
  } else if (period && computed.time_range) {
    out.push({ key: "period", value: period, label: `training period, by ${clean(computed.time_range.column)}` });
  }
  if (typeof computed.target_rate === "number" && Number.isFinite(computed.target_rate) && computed.split_plan) {
    // The read does not name the positive value, so it is described, never guessed.
    out.push({ key: "rate", value: percent(computed.target_rate), label: `of rows: ${clean(computed.split_plan.target_column)} is the answer the model treats as yes · training period` });
  }
  return out;
}

// --- V7 UI-C: columns filter, why, assistant vs rules, versions, upload ---

/** Leakage "no" (red): left out because it may give away the answer; ID, entity and risk-free columns are not leakage. */
export const isLeakageLeftOut = (c: ProfileColumnLike): boolean => leakageExcluded([c]).length === 1;

/** Columns whose name contains the filter text (case-insensitive); an empty filter keeps all. */
export function filterColumns<T extends { name: string }>(columns: T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  return q ? columns.filter((c) => c.name.toLowerCase().includes(q)) : columns;
}

/**
 * "Why" of a column: the plain reason, plus, for a column left out for leakage, the leakage reason the run recorded. The
 * recorded role reason is engine wording, so it stays in the technical details (`recordedReason`) unless no plain reason exists.
 */
export function columnWhy(
  c: ProfileColumnLike & { role_reason?: string | null; leakage_reason?: string | null }, hasRun: boolean, clean: (v: string, max?: number) => string = (v) => v,
): { text: string | null; recorded: string | null } {
  const { reason } = columnUse(c, hasRun);
  const leak = hasRun && isLeakageLeftOut(c) && c.leakage_reason?.trim() ? clean(c.leakage_reason, 200) : null;
  if (reason) return { text: reason, recorded: leak && leak !== reason ? leak : null };
  return { text: leak ?? recordedReason(c, hasRun, clean), recorded: null };
}

/** The reason the run recorded for a column (leakage reason when it was left out for leakage, else the role reason); null before a run. */
export function recordedReason(c: ProfileColumnLike & { role_reason?: string | null; leakage_reason?: string | null }, hasRun: boolean, clean: (v: string, max?: number) => string = (v) => v): string | null {
  if (!hasRun) return null;
  const raw = c.leakage_excluded && c.leakage_reason?.trim() ? c.leakage_reason : c.role_reason;
  return raw && raw.trim() ? clean(raw, 240) : null;
}

export type AiVsRules = { tone: "ok" | "warn" | "ai"; pill: string; text: string };
type DecisionRecordLike = { decision_type: string; effective_state: string; subject: { kind: string; id: string }; details?: Record<string, unknown> };
const POINT_WORDS: Record<string, string> = {
  "column.is_identifier": "ID column", "column.semantic_role": "Kind of column", "column.missing_value_action": "Empty values", "feature.leakage_suspect": "Leakage",
};
const answerWords = (value: unknown, clean: (v: string, max?: number) => string): string =>
  value === true ? "yes" : value === false ? "no" : typeof value === "string" && value.trim() ? clean(value.replaceAll("_", " "), 40) : typeof value === "number" ? String(value) : "no answer";

/**
 * "Assistant vs rules" per column, from the run's `decision_point_resolved` records (rule answer beside the assistant's).
 * Returns null (the column is hidden) unless AI is known to be ON now (`aiOff === false`; off or unknown hides it) AND a
 * column of the run has an assistant answer that agreed or disagreed. A Map keyed by column name: names such as
 * `__proto__` are ordinary keys.
 */
export function assistantVsRules(
  records: DecisionRecordLike[], experimentId: string | null | undefined, aiOff: boolean | null, clean: (v: string, max?: number) => string = (v) => v,
): Map<string, AiVsRules> | null {
  if (!experimentId || aiOff !== false) return null;
  const parts = new Map<string, { agree: boolean; disagree: string | null; lines: string[] }>();
  for (const r of records) {
    if (r.decision_type !== "decision_point_resolved" || r.subject.kind !== "experiment" || r.subject.id !== experimentId || r.effective_state === "superseded") continue;
    const point = typeof r.details?.decision_point === "string" ? r.details.decision_point : "";
    const columns = Array.isArray(r.details?.columns) ? (r.details.columns as Array<Record<string, unknown>>) : [];
    for (const row of columns) {
      if (!row || typeof row.column !== "string" || (row.agreement !== "agree" && row.agreement !== "disagree")) continue;
      const entry = parts.get(row.column) ?? { agree: false, disagree: null, lines: [] };
      if (row.agreement === "agree") entry.agree = true;
      else entry.disagree = entry.disagree === "human_pending" ? entry.disagree : String(row.source ?? "");
      const what = Object.hasOwn(POINT_WORDS, point) ? POINT_WORDS[point] : "Answer";
      entry.lines.push(`${what}: assistant ${answerWords(row.ai, clean)}, rules ${answerWords(row.rule, clean)}.`);
      parts.set(row.column, entry);
    }
  }
  if (parts.size === 0) return null;
  const out = new Map<string, AiVsRules>();
  for (const [column, p] of parts) {
    const text = p.lines.slice(0, 4).join(" ");
    if (p.disagree === null) out.set(column, { tone: "ok", pill: "both agreed", text });
    else if (p.disagree === "human_pending") out.set(column, { tone: "warn", pill: "they disagreed: we asked you", text });
    else if (p.disagree === "human") out.set(column, { tone: "warn", pill: "they disagreed: you decided", text });
    else if (p.disagree === "ai" || p.disagree === "ai_inherited") out.set(column, { tone: "ai", pill: "they disagreed: the assistant's answer was used", text });
    else out.set(column, { tone: "warn", pill: "they disagreed: the rules' answer was used", text });
  }
  return out;
}

/** "12 weeks ago" from an ISO time; "—" when it is not a valid time. */
export function relativeTime(iso: string | null | undefined, now: number = Date.now()): string {
  const t = new Date(iso ?? "").getTime();
  if (Number.isNaN(t)) return "—";
  const minutes = Math.floor((now - t) / 60_000);
  const plural = (n: number, unit: string) => `${n} ${unit}${n === 1 ? "" : "s"} ago`;
  if (minutes < 1) return "just now";
  if (minutes < 60) return plural(minutes, "minute");
  if (minutes < 1440) return plural(Math.floor(minutes / 60), "hour");
  const days = Math.floor(minutes / 1440);
  if (days < 14) return plural(days, "day");
  return days < 365 ? plural(Math.floor(days / 7), "week") : plural(Math.floor(days / 365), "year");
}

/** Who uploaded a file, in words (no ids or names are returned by the API). */
export function uploaderText(uploader: { kind: string; is_you?: boolean } | null | undefined): string {
  if (!uploader) return "Unknown";
  // The read does not say whether that person is still a member, so only "a person" is claimed.
  if (uploader.kind === "person") return uploader.is_you ? "You" : "A person";
  if (uploader.kind === "connected_tool") return "A connected tool";
  if (uploader.kind === "agent") return "An agent";
  if (uploader.kind === "rule") return "DCLab's rules";
  return "Unknown";
}

export type VersionStatus = { inUse: boolean; newer: boolean; text: string };
/** Status of one version: in use (the project's data), newer than the data in use (an upload made later), made by a run, and what uses it. */
export function versionStatus(
  d: { id: string; created_at: string }, ctx: { inUse: { id: string; created_at: string } | null; prepared: boolean; used: UsedBy | null },
): VersionStatus {
  const inUse = ctx.inUse?.id === d.id;
  const newer = !inUse && !ctx.prepared && !!ctx.inUse && new Date(d.created_at).getTime() > new Date(ctx.inUse.created_at).getTime();
  const used = ctx.used ? usedByText(ctx.used) : null;
  const lead = inUse ? "The data in use." : newer ? "Newer than the data in use." : ctx.prepared ? "Made by a run from an uploaded file." : "";
  // A table a run made is used by that run's own steps, which the graph does not list as uses: say nothing rather than "not used".
  const tail = used === null || (ctx.prepared && used === "Not used yet") ? "" : used === "Not used yet" ? "Not used yet." : `Used by ${used}.`;
  return { inUse, newer, text: [lead, tail].filter(Boolean).join(" ") || "—" };
}

export type UploadError = { title: string; detail: string };
const UPLOAD_ERRORS: Record<string, UploadError> = {
  upload_rejected: { title: "That file could not be read as a table", detail: "Nothing was added. Use a CSV, TSV, JSON, Parquet or XLSX file with a header row." },
  payload_too_large: { title: "The file is too large", detail: "Nothing was added. Upload a smaller file." },
  length_required: { title: "The upload could not be sent", detail: "Nothing was added. Try again; if it keeps failing, use a smaller file." },
  quota_exceeded: { title: "Your workspace has no storage left", detail: "Nothing was stored. Ask an admin of this workspace to free space or raise the limit." },
  idempotency_key_conflict: { title: "That upload may already be saved", detail: "Look at the list of versions before you upload again." },
  validation_failed: { title: "The upload was refused", detail: "Nothing was added. Choose one file and try again." },
};

/** Upload refusal -> a fixed plain sentence keyed by the API code (never the server's own message). */
export function uploadErrorText(error: unknown): UploadError {
  const body = (error as { body?: { error?: { code?: unknown } } } | null)?.body;
  const code = typeof body?.error?.code === "string" ? body.error.code.toLowerCase() : "";
  const status = (error as { status?: unknown } | null)?.status;
  if (Object.hasOwn(UPLOAD_ERRORS, code)) return UPLOAD_ERRORS[code];
  if (status === 413) return UPLOAD_ERRORS.payload_too_large;
  if (status === 403) return { title: "You cannot upload here", detail: "Uploading needs a role that can change ML work in this workspace." };
  if (status === 404) return { title: "This project is not in the active workspace", detail: "Switch to the project's workspace and try again." };
  if (status === 422 || status === 400) return UPLOAD_ERRORS.validation_failed;
  if (status === 429) return { title: "Too many uploads right now", detail: "Nothing was added. Wait a minute and try again." };
  return { title: "Could not confirm the upload", detail: "Look at the list of versions before you upload again." };
}

export type CheckRow = { key: string; label: string; result: "ok" | "info" | "attention" | "unknown"; text: string };
type FindingBrief = { check: string; status: string; message: string };
export type CheckContext = { findingsLoading?: boolean; findingsError?: boolean };

const findingResult = (status: string): CheckRow["result"] => (status === "pass" ? "ok" : status === "not_evaluated" ? "unknown" : "attention");

/** Duplicates, missing values and leakage in plain words, from the training-row profile and the newest run's trust checks. */
export function dataCheckRows(
  profile: ProfileLike & { columns: Array<ProfileColumnLike & { leakage_reason?: string | null }> },
  findings: FindingBrief[] | null | undefined,
  clean: (v: string, max?: number) => string = (v) => v,
  ctx: CheckContext = {},
): CheckRow[] {
  const computed = profile.statistics_status === "computed";
  const missing = profile.columns.filter((c) => (c.missing_fraction ?? 0) > 0).sort((a, b) => (b.missing_fraction ?? 0) - (a.missing_fraction ?? 0));
  const dup = findings?.find((f) => f.check === "duplicate_rows");
  const leak = findings?.find((f) => f.check === "target_leakage");
  const excluded = leakageExcluded(profile.columns);
  const top = missing.slice(0, 4).map((c) => `${clean(c.name, 40)} is empty for ${percent(c.missing_fraction)} of rows`);
  const none = !profile.experiment ? "Checked inside each run. No finished run yet."
    : ctx.findingsLoading ? "Loading the run's checks…" : ctx.findingsError ? "The run's checks could not be loaded." : "This run recorded no such check.";
  const left = excluded.length ? `Left out so they cannot give away the answer: ${excluded.slice(0, 6).map((c) => clean(c.name, 40)).join(", ")}${excluded.length > 6 ? ` and ${excluded.length - 6} more` : ""}.` : "";
  return [
    dup ? { key: "dup", label: "Duplicates", result: findingResult(dup.status), text: clean(dup.message, 300) }
      : { key: "dup", label: "Duplicates", result: "unknown", text: none },
    computed
      ? { key: "missing", label: "Missing values", result: missing.length ? "info" : "ok", text: missing.length ? `${top.join("; ")}${missing.length > 4 ? `; ${missing.length - 4} more columns have some empty values` : ""}. Counted on training rows only.` : "No column has empty values in the training rows." }
      : { key: "missing", label: "Missing values", result: "unknown", text: profile.statistics_status === "unavailable" ? "Withheld: the test design's row map could not be verified." : "Counted after the first run fixes the test design." },
    leak ? { key: "leak", label: "Leakage", result: findingResult(leak.status), text: [clean(leak.message, 300), left].filter(Boolean).join(" ") || "See the trust checks below." }
      : excluded.length ? { key: "leak", label: "Leakage", result: "attention", text: left }
        : { key: "leak", label: "Leakage", result: "unknown", text: none },
  ];
}
