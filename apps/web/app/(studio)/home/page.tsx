"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { FlowTiles } from "@/components/studio/FlowBar";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { GoalCell, LastRunCell } from "@/app/components/studio-app/ProjectCells";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { useSession, useStudioProjects, type StudioProjectListItem } from "@/lib/application";
import { isUuid, plainText } from "@/lib/application/command-search";
import { utcWhen } from "@/lib/application/format-time";
import { STUDIO_BACKEND_FEATURES } from "@/lib/application/studio-navigation";
import { useProjectFlow } from "@/lib/application/studio-flow-hooks";
import { flowTargets, flowTiles, FLOW_TILE_ORDER } from "@/lib/application/studio-flow-tiles";
import { useActivity, useAiOff, type StudioActivityItem } from "@/lib/application/studio-home-hooks";
import {
  activityHref, activitySentence, actorLabel, championScoreText, foldMeter, greeting, homeSubtitle, isAssistantRow, liveRunText, liveRuns, otherRunsText, runningCount, runningSentence,
  needsYouRow, projectHomeHref, recentProjects, runResultText,
} from "@/lib/application/studio-home";
import { useInbox, useInboxCounts, useRunNamer, type StudioInboxItem } from "@/lib/application/studio-inbox-hooks";
import { modelInUseText } from "@/lib/application/studio-projects";
import { safeInternalHref } from "@/components/studio/safe-href";
import { displayName } from "@/lib/infrastructure/session";

const HOME_PROJECTS = 8;
const INBOX_PREVIEW = 5;
/** Steps whose screen exists (the sidebar's flags); History is listed when the decision log is. */
const TILE_AVAILABLE: ReadonlySet<string> = new Set(FLOW_TILE_ORDER.map((s) => s.id).filter((id) => STUDIO_BACKEND_FEATURES[id === "history" ? "decisions" : id] === true));

/** "Now" for the "started … ago" words, refreshed every half minute (never read during render on the server). */
function useNow(): Date | null {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    setNow(new Date());
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);
  return now;
}

function ProjectRow({ project }: { project: StudioProjectListItem }) {
  const href = projectHomeHref(project);
  const score = championScoreText(project);
  const name = plainText(project.name, 80);
  return (
    <tr>
      <td>{href ? <Link href={href}><b>{name}</b></Link> : <b>{name}</b>}</td>
      <td><GoalCell project={project} /></td>
      <td className="r num">{score ?? <span className="muted">—</span>}</td>
      <td>{modelInUseText(project.summary) ?? <span className="muted">None yet</span>}</td>
      <td><LastRunCell project={project} /></td>
    </tr>
  );
}

function NeedsYouRow({ item, index, projectName }: { item: StudioInboxItem; index: number; projectName: string | null }) {
  const runName = useRunNamer(item.project_id);
  const run = item.subject.kind === "experiment" && isUuid(item.subject.id) ? runName(item.subject.id) : null;
  const row = needsYouRow(item, { projectName, runName: run });
  const href = safeInternalHref(row.action.href) ?? "/inbox";
  return (
    <li>
      <span className="v7-home-ic" aria-hidden="true">{index + 1}</span>
      <span className="v7-home-row-text">
        <b>{row.title}</b>
        <small className="muted">{row.sentence} {utcWhen(item.occurred_at)}</small>
      </span>
      <Link className="btn" href={href}>{row.action.label}</Link>
    </li>
  );
}

function ActivityRow({ item, projectName }: { item: StudioActivityItem; projectName: string | null }) {
  const runName = useRunNamer(item.project_id);
  const run = item.link.kind === "experiment" && isUuid(item.link.id) ? runName(item.link.id) : null;
  const href = activityHref(item);
  const actor = actorLabel(item.actor);
  const result = item.kind === "run_finished" ? runResultText(item.run_result) : null;
  const sentence = `${activitySentence(item, run)}${result ? ` · ${result}` : ""}`;
  return (
    <li>
      <Pill tone={actor.tone}>{actor.text}</Pill>{" "}
      {href ? <Link href={href}>{sentence}</Link> : sentence}
      {projectName ? <span className="muted"> · {projectName}</span> : null}{" "}
      <small className="muted">{utcWhen(item.occurred_at)}</small>
    </li>
  );
}

/** The most recently changed project as its eight steps, each with a value and a note from the flow read. */
function ActiveProject({ project }: { project: StudioProjectListItem }) {
  const flow = useProjectFlow(project.id);
  const goal = project.summary?.goal;
  const tiles = flow.data ? flowTiles(flow.data, { projectId: project.id, pathname: "/home", available: TILE_AVAILABLE, targets: flowTargets(flow.data) }) : null;
  return (
    <Card title={plainText(project.name, 80)} aside={goal?.objective ? plainText(goal.objective, 120) : "Your latest project"}>
      {flow.isPending ? <p role="status">Loading the project&apos;s steps…</p> : null}
      {flow.isError ? <QueryNotice error={flow.error} what="project's steps" /> : null}
      {tiles ? <FlowTiles steps={tiles} label="Steps of the active project" /> : null}
      <p className="toolbar"><Link className="btn primary" href={projectHomeHref(project) ?? "/projects"}>Open project</Link></p>
    </Card>
  );
}

/** "Next weekly scoring of …" for the latest project, only when its schedule is on and the flow read names the time. */
function WeeklyLine({ project }: { project: StudioProjectListItem }) {
  const flow = useProjectFlow(project.id);
  const next = flow.data?.predictions.schedule_enabled === true ? flow.data.predictions.next_planned_for : null;
  return next ? <p className="muted">Weekly scoring of {plainText(project.name, 60)} next runs {utcWhen(next)}.</p> : null;
}

export default function StudioHomePage() {
  const session = useSession();
  const projects = useStudioProjects();
  const counts = useInboxCounts();
  const inbox = useInbox("needs_decision", INBOX_PREVIEW);
  const activity = useActivity();
  const aiOff = useAiOff();
  const now = useNow();

  const list = projects.data ?? [];
  const recent = recentProjects(list, HOME_PROJECTS);
  const live = liveRuns(list);
  const running = projects.data ? runningCount(list) : undefined;
  const names = new Map(list.map((p) => [p.id, plainText(p.name, 60)]));
  const nameOf = (id: string | null | undefined) => (id ? (names.get(id) ?? null) : null);
  const feed = (activity.data?.pages.flatMap((page) => page.items) ?? []).filter((item) => !(aiOff === true && isAssistantRow(item)));
  const preview = inbox.data?.pages[0]?.items.slice(0, INBOX_PREVIEW) ?? [];
  const waiting = counts.data?.needs_decision;
  const first = session.user ? displayName(session.user).split(/\s+/)[0] : null;
  // The greeting follows the viewer's local hour, so it is set after mount (a stable "Hello" first: no hydration mismatch).
  const hello = `${now ? greeting(now.getHours()) : "Hello"}${first ? `, ${plainText(first, 40)}` : ""}`;

  return (
    <>
      <PageHead
        eyebrow="Home" title={hello}
        subtitle={homeSubtitle(waiting, running) || undefined}
        actions={<Link className="btn primary" href="/projects/new">Upload data</Link>}
      />
      <PageGuide
        purpose="A starting point for your workspace: what needs you, where your latest project stands, what is running and what happened lately."
        howTo={<>Start with what needs you, then open your project or the Inbox. Upload data starts a new project from a file. Press ⌘K (Ctrl+K) to jump to a project, run or model by name. Every value here is read from your workspace; nothing is estimated.</>}
        youGet="The decisions waiting for you, your latest project as its eight steps, runs in progress with the fold they are on, recent activity as sentences, and your projects."
        attention={waiting ? `${waiting} ${waiting === 1 ? "thing is" : "things are"} waiting for your decision in the Inbox.` : undefined}
      />
      {aiOff === true ? <Banner tone="info">AI suggestions are switched off. Everything on this page works with the rules alone.</Banner> : null}

      <Card title={waiting === undefined ? "What needs you" : `${waiting} ${waiting === 1 ? "thing needs" : "things need"} you`} aside={<Link href="/inbox">Open the Inbox</Link>}>
        {counts.isError ? <Banner tone="warn">Could not load how many things need you. The list below may still be right; open the Inbox to check.</Banner> : null}
        {inbox.isError ? <QueryNotice error={inbox.error} what="Inbox" /> : null}
        {inbox.isPending ? <p role="status">Loading the Inbox…</p> : null}
        {inbox.data && preview.length === 0 ? <div className="empty">Nothing is waiting for you.</div> : null}
        {preview.length > 0 ? (
          <ol className="v7-home-needs" aria-label="Waiting for you">
            {preview.map((item, index) => <NeedsYouRow key={item.id} item={item} index={index} projectName={nameOf(item.project_id)} />)}
          </ol>
        ) : null}
      </Card>

      {projects.isError ? <QueryNotice error={projects.error} what="project list" /> : null}
      {projects.isPending ? <p role="status">Loading projects…</p> : null}
      {projects.data && recent.length === 0 ? (
        <Card title="Your first project">
          <div className="empty"><p><b>Create your first project.</b></p><p>A project holds your data, runs, models and the decisions that changed them.</p><p><Link className="btn primary" href="/projects/new">New project</Link></p></div>
        </Card>
      ) : null}
      {recent[0] ? <ActiveProject project={recent[0]} /> : null}

      <div className="grid cols-2">
        <Card title="Running now" aside={running?.kind === "all" ? `${running.runs} not finished` : running ? "latest runs only" : undefined}>
          {projects.isPending ? <p role="status">Loading runs…</p> : null}
          {running && live.length === 0 ? <div className="empty">{runningSentence(running)}</div> : null}
          {live.length > 0 ? (
            <ul className="v7-home-runs" aria-label="Runs in progress">
              {live.map(({ project, run, experimentId, others }) => {
                const href = isUuid(project.id) && experimentId ? safeInternalHref(`/projects/${project.id}/experiments/${experimentId}`) : projectHomeHref(project);
                const text = run ? liveRunText(run, now) : { title: "Runs not finished", detail: otherRunsText(others, false) };
                const meter = run ? foldMeter(run.progress) : null;
                return (
                  <li key={project.id}>
                    <span className="v7-home-ic" aria-hidden="true">▶</span>
                    <span className="v7-home-row-text">
                      <b>{text.title}</b>
                      <small className="muted">{plainText(project.name, 60)} · {text.detail}</small>
                      {run && others ? <small className="muted">{otherRunsText(others, true)}.</small> : null}
                      {meter ? (
                        <span className="meter" role="progressbar" aria-label={`Model being trained in ${text.title}: folds done`} aria-valuemin={0} aria-valuemax={meter.max} aria-valuenow={meter.value} aria-valuetext={meter.text}>
                          <i style={{ width: `${Math.round((meter.value / meter.max) * 100)}%` }} />
                        </span>
                      ) : null}
                    </span>
                    {href ? <Link className="btn" href={href}>Open</Link> : null}
                  </li>
                );
              })}
            </ul>
          ) : null}
          {recent[0] ? <WeeklyLine project={recent[0]} /> : null}
        </Card>
        <Card title="Recent activity" aside="newest first">
          {activity.isError ? <QueryNotice error={activity.error} what="activity feed" /> : null}
          {activity.isPending ? <p role="status">Loading activity…</p> : null}
          {activity.data && feed.length === 0 ? <div className="empty">Nothing has happened yet. Decisions and runs appear here.</div> : null}
          {feed.length > 0 ? (
            <ul className="plain-list v7-home-hist" aria-label="Recent activity">
              {feed.map((item) => <ActivityRow key={item.id} item={item} projectName={nameOf(item.project_id)} />)}
            </ul>
          ) : null}
          {activity.hasNextPage ? <p><button type="button" className="btn" disabled={activity.isFetchingNextPage} onClick={() => void activity.fetchNextPage()}>{activity.isFetchingNextPage ? "Loading…" : "Load more"}</button></p> : null}
        </Card>
      </div>

      <Card title="All projects" aside={<Link href="/projects">See all</Link>}>
        {projects.isError ? <QueryNotice error={projects.error} what="project list" /> : null}
        {recent.length > 0 ? (
          <div className="tbl">
            <table>
              <caption className="sr-only">Projects, most recently changed first</caption>
              <thead><tr><th scope="col">Project</th><th scope="col">Goal</th><th scope="col" className="r">CV score (model in use)</th><th scope="col">Model in use</th><th scope="col">Last run</th></tr></thead>
              <tbody>{recent.map((project) => <ProjectRow key={project.id} project={project} />)}</tbody>
            </table>
          </div>
        ) : null}
        {projects.data && recent.length === 0 ? <div className="empty">No projects yet.</div> : null}
        <div className="toolbar"><Link className="btn" href="/projects/new">New project</Link><Link className="btn" href="/agents">Connect</Link></div>
      </Card>
    </>
  );
}
