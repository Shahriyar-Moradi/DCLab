"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { Banner } from "@/components/studio/Banner";
import { FlowBar } from "@/components/studio/FlowBar";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { useProjectExperiments, useProjectRefs, useStudioProject } from "@/lib/application";
import { STUDIO_BACKEND_FEATURES } from "@/lib/application/studio-navigation";
import { buildFlow, flowRunStatuses } from "@/lib/application/studio-flow";
import { ApiError } from "@/lib/infrastructure/api-client";

/** "No backend, no element": an explicit empty state that names the prompt that ships it. */
export function PhaseEmpty({ title, phase, children }: { title: string; phase: string; children?: ReactNode }) {
  return (
    <div className="empty" role="note">
      <p><b>{title}</b></p>
      <p>Planned in {phase}.{children ? " " : ""}{children}</p>
    </div>
  );
}

export function QueryNotice({ error, what }: { error: unknown; what: string }) {
  if (error instanceof ApiError && (error.status === 404 || error.status === 403)) {
    return <Banner tone="warn">This {what} does not exist in the active workspace, or you cannot see it.</Banner>;
  }
  return <Banner tone="crit">Could not load the {what}. {error instanceof Error ? error.message : ""}</Banner>;
}

export function formatWhen(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString();
}

export const STATUS_TONE: Record<string, PillTone> = {
  completed: "ok", running: "ai", queued: "gray", cancelling: "warn", needs_input: "warn",
  failed: "crit", cancelled: "gray", skipped: "gray", accepted: "ok", proposed: "warn", rejected: "crit", superseded: "gray",
};

/** Pill tone of a run status; own keys only (a status such as `__proto__` is gray). */
export const statusTone = (status: string): PillTone => (Object.hasOwn(STATUS_TONE, status) ? STATUS_TONE[status] : "gray");

const REF_LABEL: Record<string, string> = {
  champion_model: "Model in use", problem_spec: "Goal", split_plan: "Test design", dataset: "Data", feature_recipe: "Features",
};

const FLOW_STEPS = ["data", "goal", "experiments", "improve", "models", "predictions", "monitoring"] as const;
const FLOW_AVAILABLE: ReadonlySet<string> = new Set(FLOW_STEPS.filter((id) => STUDIO_BACKEND_FEATURES[id] === true));

/** Project band above every project page: name, the project's steps (flow bar), and the versions in use behind a details row. */
export function ProjectHeader({ projectId, children }: { projectId: string; children: ReactNode }) {
  const project = useStudioProject(projectId);
  const refs = useProjectRefs(projectId);
  const runs = useProjectExperiments(projectId);
  const pathname = usePathname();
  const tab = useSearchParams().get("tab");
  if (project.isPending) return <p role="status">Loading project…</p>;
  if (project.isError) {
    return (
      <>
        <QueryNotice error={project.error} what="project" />
        <p><Link href="/projects">Back to projects</Link></p>
      </>
    );
  }
  const items = refs.data?.items ?? [];
  const champion = items.some((ref) => ref.ref_kind === "champion_model");
  const targetOf = (kind: string) => items.find((ref) => ref.ref_kind === kind)?.target.id ?? null;
  const flow = buildFlow({
    projectId, pathname, available: FLOW_AVAILABLE,
    refKinds: refs.data?.refs_initialized ? items.map((ref) => ref.ref_kind) : refs.data ? [] : null,
    runStatuses: flowRunStatuses(runs.data?.items, !!runs.data?.next_cursor), tab,
    targets: { split_plan: targetOf("split_plan"), champion_model: targetOf("champion_model") },
  });
  return (
    <>
      <section className="toolbar" aria-label="Project">
        <span className="eyebrow">Project · {project.data.name}</span>
        {refs.isError ? <Pill tone="gray">Versions unavailable</Pill> : null}
        {refs.data && !refs.data.refs_initialized ? <Pill tone="gray">Nothing in use yet</Pill> : null}
        {champion ? <Pill tone="ok">A model is in use</Pill> : null}
        {refs.data?.refs_initialized && !champion ? <Pill tone="gray">No model in use yet</Pill> : null}
      </section>
      <FlowBar steps={flow} />
      {items.length > 0 ? (
        <details className="ids">
          <summary>Versions in use (details)</summary>
          <p className="toolbar">
            {items.map((ref) => (
              <Pill key={ref.ref_kind} tone={ref.ref_kind === "champion_model" ? "ok" : "det"}>
                {REF_LABEL[ref.ref_kind] ?? ref.ref_kind} <span className="mono">{ref.target.id.slice(0, 8)}</span>
                <span className="sr-only"> version {ref.version}</span>
              </Pill>
            ))}
          </p>
        </details>
      ) : null}
      {children}
    </>
  );
}
