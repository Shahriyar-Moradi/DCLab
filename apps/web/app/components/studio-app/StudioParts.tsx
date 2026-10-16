"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { Banner } from "@/components/studio/Banner";
import { FlowBar, FlowTiles } from "@/components/studio/FlowBar";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { useProjectExperiments, useProjectRefs, useStudioProject } from "@/lib/application";
import { STUDIO_BACKEND_FEATURES } from "@/lib/application/studio-navigation";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { buildFlow, flowRunStatuses, nextStepFor, projectModelId } from "@/lib/application/studio-flow";
import { useProjectFlow } from "@/lib/application/studio-flow-hooks";
import { flowTiles } from "@/lib/application/studio-flow-tiles";
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
  alert: "crit", // V7 UI-E: a drift alert in the Inbox
};

/** Pill tone of a run status; own keys only (a status such as `__proto__` is gray). */
export const statusTone = (status: string): PillTone => (Object.hasOwn(STATUS_TONE, status) ? STATUS_TONE[status] : "gray");

const REF_LABEL: Record<string, string> = {
  champion_model: "Model in use", problem_spec: "Goal", split_plan: "Test design", dataset: "Data", feature_recipe: "Features",
};

const FLOW_STEPS = ["data", "goal", "experiments", "improve", "models", "predictions", "monitoring"] as const;
export const FLOW_AVAILABLE: ReadonlySet<string> = new Set(FLOW_STEPS.filter((id) => STUDIO_BACKEND_FEATURES[id] === true));
/** Step screens a "Next step" button may lead to: the flow steps plus History (the last one). */
const NEXT_AVAILABLE: ReadonlySet<string> = new Set([...FLOW_AVAILABLE, ...(STUDIO_BACKEND_FEATURES.decisions ? ["history"] : [])]);

/** The page's one forward button (V7 UI-A), at the bottom of each project step screen; none on the last step. */
export function NextStepLink({ step }: { step: { title: string; detail: string; href: string } }) {
  return (
    <nav aria-label="Next step">
      <Link className="next-step" href={step.href}>
        <span className="ns-label">Next step</span>
        <b>{step.title} <span aria-hidden="true">→</span></b>
        <small>{step.detail}</small>
      </Link>
    </nav>
  );
}

/** Project band above every project page: name, the project's steps (flow bar), and the versions in use behind a details row. */
export function ProjectHeader({ projectId, children }: { projectId: string; children: ReactNode }) {
  const project = useStudioProject(projectId);
  const refs = useProjectRefs(projectId);
  const runs = useProjectExperiments(projectId);
  const graph = useProjectGraph(projectId);
  const facts = useProjectFlow(projectId);
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
  const targets = { split_plan: targetOf("split_plan"), project_model: targetOf("champion_model") ?? projectModelId(graph.data?.nodes) };
  const flow = buildFlow({
    projectId, pathname, available: FLOW_AVAILABLE,
    refKinds: refs.data?.refs_initialized ? items.map((ref) => ref.ref_kind) : refs.data ? [] : null,
    runStatuses: flowRunStatuses(runs.data?.items, !!runs.data?.next_cursor), tab,
    targets,
  });
  const next = nextStepFor({ projectId, pathname, available: NEXT_AVAILABLE, targets });
  // V7 UI-B: eight tiles with a value and a note once the flow read is in; until then (or if it fails) the step names alone.
  const tiles = facts.data ? flowTiles(facts.data, { projectId, pathname, tab, available: NEXT_AVAILABLE, targets }) : null;
  return (
    <>
      <section className="toolbar" aria-label="Project">
        <span className="eyebrow">Project · {project.data.name}</span>
        {refs.isError ? <Pill tone="gray">Versions unavailable</Pill> : null}
        {refs.data && !refs.data.refs_initialized ? <Pill tone="gray">Nothing in use yet</Pill> : null}
        {champion ? <Pill tone="ok">A model is in use</Pill> : null}
        {refs.data?.refs_initialized && !champion ? <Pill tone="gray">No model in use yet</Pill> : null}
      </section>
      {tiles ? <FlowTiles steps={tiles} /> : <FlowBar steps={flow} />}
      {facts.isError ? <p className="muted v7-flow-note">The details of each step could not be loaded; the steps still open their pages.</p> : null}
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
      {next ? <NextStepLink step={next} /> : null}
    </>
  );
}
