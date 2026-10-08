"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { Banner } from "@/components/studio/Banner";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { useProjectRefs, useStudioProject } from "@/lib/application";
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

const REF_LABEL: Record<string, string> = {
  champion_model: "Champion", problem_spec: "Spec", split_plan: "Split", dataset: "Data", feature_recipe: "Features",
};

/** Project band above every project page: name plus ref badges from GET /v1/projects/{id}/refs. */
export function ProjectHeader({ projectId, children }: { projectId: string; children: ReactNode }) {
  const project = useStudioProject(projectId);
  const refs = useProjectRefs(projectId);
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
  return (
    <>
      <section className="toolbar" aria-label="Project">
        <span className="eyebrow">Project · {project.data.name}</span>
        <Term definition="A ref is a named pointer to the version the project uses now. Moving it is a recorded decision.">refs</Term>
        {refs.isError ? <Pill tone="gray">Refs unavailable</Pill> : null}
        {refs.data && !refs.data.refs_initialized ? <Pill tone="gray">No refs yet</Pill> : null}
        {items.map((ref) => (
          <Pill key={ref.ref_kind} tone={ref.ref_kind === "champion_model" ? "ok" : "det"}>
            {REF_LABEL[ref.ref_kind] ?? ref.ref_kind} <span className="mono">{ref.target.id.slice(0, 8)}</span>
            <span className="sr-only"> version {ref.version}</span>
          </Pill>
        ))}
        {refs.data?.refs_initialized && !champion ? <Pill tone="gray">No champion yet</Pill> : null}
      </section>
      {children}
    </>
  );
}
