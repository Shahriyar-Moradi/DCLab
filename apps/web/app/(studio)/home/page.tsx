"use client";

import Link from "next/link";
import { Card } from "@/components/studio/Card";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { PhaseEmpty, QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useStudioProjects } from "@/lib/application";

export default function StudioHomePage() {
  const projects = useStudioProjects();
  const recent = [...(projects.data ?? [])].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 6);
  return (
    <>
      <PageHead eyebrow="Developer Studio" title="Home" subtitle="Start from a project. Each project holds its data, experiments, models and the decisions that changed them." actions={<Link className="btn" href="/projects">All projects</Link>} />
      <PageGuide
        purpose="A starting point for your workspace."
        howTo="Open a project to see its experiments. Press ⌘K (Ctrl+K) to jump to a project, experiment, model or decision by name or id."
        youGet="Your most recently changed projects."
      />
      <Card title="Recent projects" aside={projects.data ? `${projects.data.length} in this workspace` : undefined}>
        {projects.isError ? <QueryNotice error={projects.error} what="project list" /> : null}
        {projects.isPending ? <p role="status">Loading projects…</p> : null}
        {projects.data && recent.length === 0 ? <div className="empty">No projects yet. A project is created when you upload data in Labs.</div> : null}
        {recent.length > 0 ? (
          <ul className="plain-list">
            {recent.map((project) => (
              <li key={project.id}>
                <Link href={`/projects/${project.id}/experiments`}>{project.name}</Link>{" "}
                <small className="muted">changed {formatWhen(project.updated_at)}</small>
              </li>
            ))}
          </ul>
        ) : null}
      </Card>
      <Card title="Activity and run queue">
        <PhaseEmpty title="Workspace activity arrives with the activity read model." phase="P4.15" />
      </Card>
    </>
  );
}
