/**
 * ⌘K search over existing /v1 list endpoints (P4.1-A). Pure: the data sources are injected,
 * so debounce, abort and href building run under `npm run test:components`.
 *
 * Every name the API returns is untrusted text: it is reduced to plain single-line text here
 * and rendered as React text (never HTML). Every href is built from ids that match the UUID
 * shape and then passes `safeInternalHref`; anything else is dropped.
 */
import { safeInternalHref } from "../../components/studio/safe-href.ts";

export type SearchKind = "Project" | "Experiment" | "Model" | "Decision";
export type SearchHit = { id: string; title: string; detail?: string; kind: SearchKind; href: string };

export type ProjectRow = { id: string; name: string; slug: string; status: string };
export type ExperimentRow = { id: string; status: string; intent?: string | null; project_id?: string | null };
export type DecisionRow = { id: string; project_id: string; decision_type: string; state: string };
export type RefRow = { ref_kind: string; target: { kind: string; id: string } };
export type ModelRow = { id: string; version: string; project_id?: string | null };

export type SearchSources = {
  projects(signal: AbortSignal): Promise<ProjectRow[]>;
  /** Workspace-wide when `projectId` is undefined. */
  experiments(projectId: string | undefined, signal: AbortSignal): Promise<ExperimentRow[]>;
  decisions(projectId: string, signal: AbortSignal): Promise<DecisionRow[]>;
  refs(projectId: string, signal: AbortSignal): Promise<RefRow[]>;
  /** Exact id lookup (no list endpoint for model versions); null when absent or not visible. */
  modelVersion(id: string, signal: AbortSignal): Promise<ModelRow | null>;
};

export type SearchContext = { projectId?: string };

// Lowercase only: the API emits lowercase UUIDs, so one id has one URL.
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const PER_KIND = 5;
export const MIN_QUERY_LENGTH = 2;

export function isUuid(value: unknown): value is string {
  return typeof value === "string" && UUID.test(value);
}

/** Plain single-line text: control characters removed, length capped. */
export function plainText(value: unknown, max = 120): string {
  const text = String(value ?? "").replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029]+/g, " ").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** `/projects/{id}[/section[/{child}]]` from UUIDs only; null otherwise. */
export function projectHref(projectId: unknown, section?: string, childId?: unknown): string | null {
  if (!isUuid(projectId)) return null;
  if (section !== undefined && !/^[a-z]+$/.test(section)) return null;
  if (childId !== undefined && !isUuid(childId)) return null;
  const parts = ["", "projects", projectId, section, childId as string | undefined].filter((p) => p !== undefined);
  return safeInternalHref(parts.join("/"));
}

const shortId = (id: string) => id.slice(0, 8);

function matches(query: string, ...fields: Array<string | null | undefined>): boolean {
  return fields.some((field) => typeof field === "string" && field.toLowerCase().includes(query));
}

function hit(kind: SearchKind, id: string, title: string, href: string | null, detail?: string): SearchHit | null {
  return href ? { kind, id: `${kind}:${id}`, title: plainText(title) || shortId(id), href, detail: detail ? plainText(detail, 80) : undefined } : null;
}

const settle = <T,>(promise: Promise<T>, fallback: T): Promise<T> => promise.catch((error: unknown) => {
  if ((error as { name?: unknown } | null)?.name === "AbortError") throw error;
  return fallback; // one failing source (403, 404, network) never hides the others
});

/** One search round. Throws AbortError when `signal` aborts. */
export async function searchStudio(rawQuery: string, context: SearchContext, sources: SearchSources, signal: AbortSignal): Promise<SearchHit[]> {
  const query = rawQuery.trim().toLowerCase();
  if (query.length < MIN_QUERY_LENGTH) return [];
  const projectId = isUuid(context.projectId) ? context.projectId : undefined;
  const [projects, experiments, decisions, refs, model] = await Promise.all([
    settle(sources.projects(signal), [] as ProjectRow[]),
    settle(sources.experiments(projectId, signal), [] as ExperimentRow[]),
    projectId ? settle(sources.decisions(projectId, signal), [] as DecisionRow[]) : Promise.resolve([] as DecisionRow[]),
    projectId ? settle(sources.refs(projectId, signal), [] as RefRow[]) : Promise.resolve([] as RefRow[]),
    isUuid(query) ? settle(sources.modelVersion(query, signal), null) : Promise.resolve(null),
  ]);
  signal.throwIfAborted();

  const out: Array<SearchHit | null> = [];
  out.push(...projects.filter((p) => matches(query, p.name, p.slug, p.id)).slice(0, PER_KIND)
    .map((p) => hit("Project", p.id, p.name, projectHref(p.id, "experiments"), p.status)));
  out.push(...experiments.filter((e) => matches(query, e.intent, e.id, e.status)).slice(0, PER_KIND)
    .map((e) => hit("Experiment", e.id, e.intent || `Run ${shortId(e.id)}`, projectHref(e.project_id, "experiments", e.id), e.status)));
  const models: ModelRow[] = refs
    .filter((r) => r.target.kind === "model_version" && matches(query, "model in use champion model", r.target.id))
    .map((r) => ({ id: r.target.id, version: "in use", project_id: projectId }));
  if (model && !models.some((m) => m.id === model.id)) models.push(model);
  out.push(...models.slice(0, PER_KIND).map((m) => hit("Model", m.id, `Model ${shortId(m.id)}`, projectHref(m.project_id, "models"), m.version)));
  out.push(...decisions.filter((d) => matches(query, d.decision_type, d.id, d.state)).slice(0, PER_KIND)
    .map((d) => hit("Decision", d.id, d.decision_type.replaceAll("_", " "), projectHref(d.project_id, "decisions"), d.state)));
  return out.filter((h): h is SearchHit => h !== null);
}

export type CommandSearchOptions = {
  sources: SearchSources;
  onResults: (hits: SearchHit[], query: string) => void;
  onLoading?: (loading: boolean) => void;
  onError?: (error: unknown) => void;
  delayMs?: number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
};

/** Debounced search: each new query cancels the pending timer and aborts the in-flight round. */
export function createCommandSearch(options: CommandSearchOptions) {
  const { sources, onResults, onLoading, onError, delayMs = 200 } = options;
  const setTimer = options.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms));
  const clearTimer = options.clearTimer ?? ((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>));
  let timer: unknown = null;
  let inflight: AbortController | null = null;

  function cancel() {
    if (timer !== null) clearTimer(timer);
    timer = null;
    inflight?.abort();
    inflight = null;
  }

  function search(query: string, context: SearchContext = {}) {
    cancel();
    if (query.trim().length < MIN_QUERY_LENGTH) {
      onLoading?.(false);
      onResults([], query);
      return;
    }
    onLoading?.(true);
    timer = setTimer(() => {
      timer = null;
      const controller = new AbortController();
      inflight = controller;
      searchStudio(query, context, sources, controller.signal).then(
        (hits) => {
          if (controller.signal.aborted) return;
          inflight = null;
          onLoading?.(false);
          onResults(hits, query);
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          inflight = null;
          onLoading?.(false);
          onError?.(error);
        },
      );
    }, delayMs);
  }

  return { search, cancel };
}
