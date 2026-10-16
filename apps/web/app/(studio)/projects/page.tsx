"use client";

import Link from "next/link";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { NewProjectDialog } from "@/app/components/studio-app/NewProjectDialog";
import { GoalCell, LastRunCell } from "@/app/components/studio-app/ProjectCells";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { useStudioProjects, type StudioProjectListItem } from "@/lib/application";
import { plainText } from "@/lib/application/command-search";
import { championScoreText, projectHomeHref, recentProjects } from "@/lib/application/studio-home";
import { STARTING_A_PROJECT, modelInUseText, stageOrder, whereItIs } from "@/lib/application/studio-projects";

const COLUMNS: Column<StudioProjectListItem>[] = [
  {
    key: "name", header: "Project", sortValue: (p) => p.name.toLowerCase(),
    render: (p) => {
      const href = projectHomeHref(p);
      const name = plainText(p.name, 80);
      return (
        <>
          {href ? <Link href={href}><b>{name}</b></Link> : <b>{name}</b>}
          {p.status !== "active" ? <> <Pill tone="gray">{plainText(p.status, 20)}</Pill></> : null}
          {p.description ? <><br /><small className="muted">{plainText(p.description, 120)}</small></> : null}
        </>
      );
    },
  },
  { key: "goal", header: "Goal", render: (p) => <GoalCell project={p} /> },
  {
    key: "where", header: "Where it is", sortValue: (p) => stageOrder(p.summary?.stage),
    render: (p) => {
      const pills = whereItIs(p.summary);
      return pills.length ? <span className="v7-projects-where">{pills.map((pill) => <Pill key={pill.text} tone={pill.tone}>{pill.text}</Pill>)}</span> : <span className="muted">—</span>;
    },
  },
  {
    // Not sortable: projects use different metrics, and for error metrics lower is better.
    key: "score", header: "CV score (model in use)", numeric: true,
    render: (p) => championScoreText(p) ?? <span className="muted">—</span>,
  },
  { key: "model", header: "Model in use", render: (p) => modelInUseText(p.summary) ?? <span className="muted">None yet</span> },
  { key: "run", header: "Last run", sortValue: (p) => p.summary?.latest_run?.created_at ?? "", render: (p) => <LastRunCell project={p} /> },
];

export default function ProjectsPage() {
  const projects = useStudioProjects();
  const rows = projects.data ? recentProjects(projects.data, projects.data.length) : [];
  return (
    <>
      <PageHead eyebrow="Workspace" title="Projects" subtitle="Every project in this workspace. Each one goes from a customer file to a model you can trust." actions={<NewProjectDialog />} />
      <PageGuide
        purpose="Find the project you want to work on and see where each one stands."
        howTo="Pick a project to open its experiments. Sort by name, where it is or last run. New project opens the wizard."
        youGet="Every project in the active workspace with its goal, how far it got, the cross-validation score of the model in use, and its last run."
        attention={rows.some((p) => (p.summary?.waiting_decisions ?? 0) > 0) ? "Some projects have decisions waiting for you in the Inbox." : undefined}
      />
      {projects.isError ? <QueryNotice error={projects.error} what="project list" /> : null}
      {projects.isPending ? <p role="status">Loading projects…</p> : null}
      {projects.data ? (
        <DataTable caption="Projects, most recently changed first" columns={COLUMNS} rows={rows} rowKey={(p) => p.id} emptyMessage="No projects yet. Create one with New project." />
      ) : null}
      <Card title="Starting a project">
        <p className="muted">{STARTING_A_PROJECT}</p>
      </Card>
    </>
  );
}
