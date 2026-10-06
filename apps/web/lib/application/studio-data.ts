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
    used.split_plan ? `${used.split_plan} split plan${used.split_plan === 1 ? "" : "s"}` : "",
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
