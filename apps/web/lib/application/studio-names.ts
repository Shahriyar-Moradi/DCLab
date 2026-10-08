/**
 * Names instead of codes (V7-A1). Pure helpers that turn ids and version strings into the words a data scientist
 * expects: "Run 3", "Model v2", "Test design 1". Ids stay available behind details rows; they are never the name.
 */
type Timed = { id: string; created_at: string };

/** "Run 1", "Run 2", ... by start time (oldest first, ties broken by id so the numbering is stable). */
export function runOrdinals(runs: readonly Timed[]): Map<string, number> {
  const ordered = [...runs].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id));
  return new Map(ordered.map((run, index) => [run.id, index + 1]));
}

/** "Run 3 · intent" (or just "Run 3" without an intent). `ordinal` undefined = the run is outside the loaded window. */
export function runName(ordinal: number | undefined, intent?: string | null, shortId?: string): string {
  // With a partial list (more runs exist than were loaded) numbers would change from page to page: use the short id.
  const base = shortId ? `Run ${shortId}` : ordinal === undefined ? "Run" : `Run ${ordinal}`;
  return intent ? `${base} · ${intent}` : base;
}

/** "Model v2" for a numeric version; any other version string is shown as given after "Model". */
export function modelName(version: string | null | undefined): string {
  const v = (version ?? "").trim().replace(/^v/i, "");
  return v ? `Model v${v}` : "Model";
}

/** "Test design 1", "Test design 2", ... in order of first appearance; runs sharing a design share a label. */
export function designLabels(ids: ReadonlyArray<string | null | undefined>): Map<string, string> {
  const labels = new Map<string, string>();
  for (const id of ids) if (id && !labels.has(id)) labels.set(id, `Test design ${labels.size + 1}`);
  return labels;
}

/** File name plus date for a data version, never a code: "churn.csv · 12 Jun 2026". */
export function dataVersionName(fileName: string | null | undefined, createdAt: string | null | undefined): string {
  const date = createdAt ? new Date(createdAt) : null;
  const when = date && !Number.isNaN(date.getTime()) ? date.toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : null;
  const file = (fileName ?? "").trim() || "Data file";
  return when ? `${file} · ${when}` : file;
}

/** Words for the scope of a piece of evidence: the final test set is always "(used once)". */
export function evidenceScopeLabel(scope: string): string {
  return scope === "final_holdout" ? "final test set (used once)" : scope.replaceAll("_", " ");
}

const OFF_SIDEBAR_CRUMB: Record<string, string> = { graph: "Lineage", pipeline: "Run evidence", features: "Features" };

/** Breadcrumb name for project pages reached from other pages; own keys only (a URL segment like __proto__ gets none). */
export function offSidebarCrumb(section: string | undefined): string | undefined {
  return section !== undefined && Object.hasOwn(OFF_SIDEBAR_CRUMB, section) ? OFF_SIDEBAR_CRUMB[section] : undefined;
}
