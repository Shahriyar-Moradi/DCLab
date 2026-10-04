/**
 * UI coverage matcher (P4.0-A): every operation in `contracts/v1_openapi.json`
 * must have a row in `docs/mvp/UI_COVERAGE.md`, either a Studio screen (exists or
 * planned) or "API-only by design". Pure string logic; the test reads the files.
 *
 * A row covers an operation when one of its `code` spans names it:
 * - `GET /v1/projects/{id}`: method(s) and a full path; placeholder names are
 *   ignored (`{id}` matches `{project_id}`), methods may be grouped (`GET/POST`).
 * - `…/events`, `…/{id}/revoke`, `/artifacts`: a suffix relative to the previous
 *   full path in the same row (the operation path starts with that path and ends
 *   with the suffix); without its own method it inherits the previous span's.
 * - `/v1/decisions/{id}/accept|reject|supersede`: alternatives in one segment.
 * A span with a path but no method anywhere in the row covers every method.
 */

export type ContractOperation = { method: string; path: string };

type FullEntry = { kind: "full"; methods: string[] | null; path: string };
type RelativeEntry = { kind: "relative"; methods: string[] | null; base: string; suffix: string };
export type CoverageEntry = FullEntry | RelativeEntry;

const METHOD_PREFIX = /^([A-Z]+(?:\/[A-Z]+)*)\s+(\S+)/;
const RELATIVE_PREFIX = /^(?:…|\.\.\.)(?=\/)/;

/** `{project_id}` → `{}`, no trailing slash. */
export function normalizePath(path: string): string {
  const normalized = path.replace(/\{[^}]*\}/g, "{}");
  return normalized.length > 1 ? normalized.replace(/\/+$/, "") : normalized;
}

/** `/a/x|y/{id}` → [`/a/x/{id}`, `/a/y/{id}`]. */
function expandAlternatives(path: string): string[] {
  let results = [""];
  for (const [index, segment] of path.split("/").entries()) {
    const options = segment.split("|");
    results = results.flatMap((prefix) => options.map((option) => (index === 0 ? option : `${prefix}/${option}`)));
  }
  return results;
}

export function contractOperations(contract: { paths?: Record<string, Record<string, unknown>> }): ContractOperation[] {
  const operations: ContractOperation[] = [];
  for (const [path, methods] of Object.entries(contract.paths ?? {})) {
    for (const method of Object.keys(methods)) {
      operations.push({ method: method.toUpperCase(), path });
    }
  }
  return operations;
}

export function coverageEntries(markdown: string): CoverageEntry[] {
  const entries: CoverageEntry[] = [];
  for (const line of markdown.split(/\r?\n/)) {
    if (!line.trimStart().startsWith("|")) continue;
    let base: string | null = null;
    let methods: string[] | null = null;
    for (const match of line.matchAll(/`([^`]+)`/g)) {
      const span = match[1].trim();
      const methodMatch = METHOD_PREFIX.exec(span);
      const spanMethods = methodMatch ? methodMatch[1].split("/") : null;
      const target = methodMatch ? methodMatch[2] : span.split(/\s+/)[0];
      if (spanMethods) methods = spanMethods;
      if (target.startsWith("/v1/")) {
        base = normalizePath(target.split("|")[0]);
        for (const path of expandAlternatives(target)) {
          entries.push({ kind: "full", methods, path: normalizePath(path) });
        }
        continue;
      }
      const relative = target.replace(RELATIVE_PREFIX, "");
      if (base === null || !relative.startsWith("/")) continue;
      for (const suffix of expandAlternatives(relative)) {
        entries.push({ kind: "relative", methods, base, suffix: normalizePath(suffix) });
      }
    }
  }
  return entries;
}

function covers(entry: CoverageEntry, operation: ContractOperation): boolean {
  if (entry.methods && !entry.methods.includes(operation.method)) return false;
  const path = normalizePath(operation.path);
  if (entry.kind === "full") return entry.path === path;
  return path.startsWith(`${entry.base}/`) && path.endsWith(entry.suffix);
}

/** `"METHOD /path"` for every contract operation no coverage row names. */
export function uncoveredOperations(operations: ContractOperation[], markdown: string): string[] {
  const entries = coverageEntries(markdown);
  return operations
    .filter((operation) => !entries.some((entry) => covers(entry, operation)))
    .map((operation) => `${operation.method} ${operation.path}`);
}
