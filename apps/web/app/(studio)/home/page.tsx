"use client";

import Link from "next/link";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Stat } from "@/components/studio/Stat";
import { Term } from "@/components/studio/Term";
import { QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useStudioProjects, type StudioProjectListItem } from "@/lib/application";
import { plainText } from "@/lib/application/command-search";
import { useActivity, useGovernance } from "@/lib/application/studio-home-hooks";
import { actorLabel, activityHref, aiHealth, aiHealthUnavailable, championMetric, projectHomeHref, recentProjects, runsInProgress, spendSummary } from "@/lib/application/studio-home";
import { useInbox, useInboxCounts } from "@/lib/application/studio-inbox-hooks";
import { lookup } from "@/lib/application/studio-inbox";

const HOME_PROJECTS = 8;
const INBOX_PREVIEW = 5;

function ProjectRow({ project }: { project: StudioProjectListItem }) {
  const href = projectHomeHref(project);
  const goal = project.summary?.goal;
  const metric = championMetric(project);
  const run = project.summary?.latest_run;
  return (
    <tr>
      <td>{href ? <Link href={href}>{plainText(project.name, 80)}</Link> : plainText(project.name, 80)}</td>
      <td>{goal ? <>{goal.target_column ? <span className="mono">{plainText(goal.target_column, 40)}</span> : null}{goal.objective ? <span className="muted"> {plainText(goal.objective, 120)}</span> : null}{!goal.target_column && !goal.objective ? <span className="muted">Goal set, no target yet</span> : null}</> : <span className="muted">No goal yet</span>}</td>
      <td className="r">{metric ? <><span className="mono">{metric.value.toFixed(3)}</span> <span className="muted">{plainText(metric.name, 24)} (cross-validation)</span></> : <span className="muted">No model in use yet</span>}</td>
      <td>{run ? <Pill tone={lookup(STATUS_TONE, run.status, "gray")}>{run.status.replaceAll("_", " ")}</Pill> : <span className="muted">No runs yet</span>}</td>
      <td>{formatWhen(project.updated_at)}</td>
    </tr>
  );
}

export default function StudioHomePage() {
  const projects = useStudioProjects();
  const counts = useInboxCounts();
  const inbox = useInbox("needs_decision", INBOX_PREVIEW);
  const activity = useActivity();
  const governance = useGovernance();

  const list = projects.data ?? [];
  const recent = recentProjects(list, HOME_PROJECTS);
  const live = runsInProgress(list);
  const feed = activity.data?.pages.flatMap((page) => page.items) ?? [];
  const preview = inbox.data?.pages[0]?.items.slice(0, INBOX_PREVIEW) ?? [];
  const waiting = counts.data?.needs_decision;
  const health = governance.data ? aiHealth(governance.data) : governance.isError ? aiHealthUnavailable(governance.error) : null;
  const spend = governance.data ? spendSummary(governance.data) : null;

  return (
    <>
      <PageHead
        eyebrow="Workspace" title="Home" subtitle="Where your workspace stands: projects, what changed, and what waits for you."
        actions={<><Link className="btn primary" href="/projects/new">New project</Link><Link className="btn" href="/projects">All projects</Link></>}
      />
      <PageGuide
        purpose="A starting point for your workspace."
        howTo={<>Start with the numbers, then open a project or the inbox. Press ⌘K (Ctrl+K) to jump to a project, run or model by name. Every value here is read from your workspace; nothing is estimated.</>}
        youGet="Counts for projects, decisions waiting, runs in progress and AI health; your projects; the latest activity from the rules, the assistant and people."
        attention={waiting ? `${waiting} item${waiting === 1 ? " is" : "s are"} waiting for a decision in the inbox.` : undefined}
      />

      <section className="grid cols-4" aria-label="Workspace numbers">
        <Stat value={projects.data ? list.length : "…"} label="Projects" hint="in this workspace" />
        <Stat value={counts.data ? counts.data.needs_decision : counts.isError ? "n/a" : "…"} label="Waiting for a decision" hint={<Link href="/inbox">Open the inbox</Link>} />
        <Stat value={projects.data ? live : "…"} label="Runs in progress" hint="latest run of each project" />
        <Stat
          value={health ? health.label : "…"}
          label={<><Term definition="AI is advisory: it proposes, rules and people decide. Every path works with it off.">AI health</Term></>}
          hint={health ? <>{health.detail}{spend ? <> Spend {spend.value} {spend.hint}.</> : null}</> : undefined}
        />
      </section>
      {health?.state === "off" ? <Banner tone="info">{health.detail} Nothing is spent on AI while it is off.</Banner> : null}

      <Card title="Projects" aside={projects.data ? `${list.length} in this workspace` : undefined}>
        {projects.isError ? <QueryNotice error={projects.error} what="project list" /> : null}
        {projects.isPending ? <p role="status">Loading projects…</p> : null}
        {projects.data && list.length === 0 ? (
          <div className="empty"><p><b>Create your first project.</b></p><p>A project holds your data, runs, models and the decisions that changed them.</p><p><Link className="btn primary" href="/projects/new">New project</Link></p></div>
        ) : null}
        {recent.length > 0 ? (
          <div className="tbl">
            <table>
              <caption className="sr-only">Projects, most recently changed first</caption>
              <thead><tr><th scope="col">Project</th><th scope="col">Goal</th><th scope="col" className="r">Best score (model in use)</th><th scope="col">Latest run</th><th scope="col">Changed</th></tr></thead>
              <tbody>{recent.map((project) => <ProjectRow key={project.id} project={project} />)}</tbody>
            </table>
          </div>
        ) : null}
      </Card>

      <div className="grid cols-2">
        <Card title="Activity" aside="newest first">
          {activity.isError ? <QueryNotice error={activity.error} what="activity feed" /> : null}
          {activity.isPending ? <p role="status">Loading activity…</p> : null}
          {activity.data && feed.length === 0 ? <div className="empty">Nothing has happened yet. Decisions, runs and assistant work appear here.</div> : null}
          {feed.length > 0 ? (
            <ul className="plain-list" aria-label="Recent activity">
              {feed.map((item) => {
                const href = activityHref(item);
                const actor = actorLabel(item.actor);
                return (
                  <li key={item.id}>
                    <Pill tone={actor.tone}>{actor.text}</Pill>{" "}
                    {href ? <Link href={href}>{plainText(item.summary, 160)}</Link> : plainText(item.summary, 160)}{" "}
                    <small className="muted">{formatWhen(item.occurred_at)}</small>
                  </li>
                );
              })}
            </ul>
          ) : null}
          {activity.hasNextPage ? <p><button type="button" className="btn" disabled={activity.isFetchingNextPage} onClick={() => void activity.fetchNextPage()}>{activity.isFetchingNextPage ? "Loading…" : "Load more"}</button></p> : null}
        </Card>

        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <Card title="Inbox" aside={waiting !== undefined ? `${waiting} waiting` : undefined}>
            {inbox.isError ? <QueryNotice error={inbox.error} what="inbox" /> : null}
            {inbox.isPending ? <p role="status">Loading the inbox…</p> : null}
            {inbox.data && preview.length === 0 ? <div className="empty">Nothing is waiting for you.</div> : null}
            {preview.length > 0 ? (
              <ul className="plain-list" aria-label="Inbox preview">
                {preview.map((item) => <li key={item.id}>{plainText(item.summary, 140)} <small className="muted">{formatWhen(item.occurred_at)}</small></li>)}
              </ul>
            ) : null}
            <p><Link className="btn" href="/inbox">Open inbox</Link></p>
          </Card>
          <Card title="Quick actions">
            <div className="toolbar">
              <Link className="btn primary" href="/projects/new">New project</Link>
              <Link className="btn" href="/projects/new">Upload data</Link>
              <Link className="btn" href="/agents">Connect</Link>
            </div>
          </Card>
        </div>
      </div>
    </>
  );
}
