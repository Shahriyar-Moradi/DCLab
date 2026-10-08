"use client";

import Link from "next/link";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useStudioProjects, type StudioProject } from "@/lib/application";

const COLUMNS: Column<StudioProject>[] = [
  { key: "name", header: "Project", sortValue: (p) => p.name.toLowerCase(), render: (p) => <Link href={`/projects/${p.id}/experiments`}>{p.name}</Link> },
  { key: "status", header: "Status", render: (p) => <Pill tone={STATUS_TONE[p.status] ?? "gray"}>{p.status}</Pill> },
  { key: "description", header: "Description", render: (p) => <span className="muted">{p.description || "—"}</span> },
  { key: "updated", header: "Last change", sortValue: (p) => p.updated_at, render: (p) => formatWhen(p.updated_at) },
];

export default function ProjectsPage() {
  const projects = useStudioProjects();
  return (
    <>
      <PageHead eyebrow="Workspace" title="Projects" subtitle="A project is one prediction goal with its data, experiments, models and decisions." actions={<Link className="btn primary" href="/projects/new">New project</Link>} />
      <PageGuide
        purpose="Find the project you want to work on."
        howTo="Pick a project to open its experiments. Sort by name or last change."
        youGet="Every project in the active workspace."
      />
      {projects.isError ? <QueryNotice error={projects.error} what="project list" /> : null}
      {projects.isPending ? <p role="status">Loading projects…</p> : null}
      {projects.data ? (
        <DataTable caption="Projects" columns={COLUMNS} rows={projects.data} rowKey={(p) => p.id} emptyMessage="No projects yet. Create one with New project." />
      ) : null}
    </>
  );
}
