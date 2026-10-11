"use client";

import Link from "next/link";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { FlowBar } from "@/components/studio/FlowBar";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { FLOW_AVAILABLE, QueryNotice, STATUS_TONE, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments, useProjectRefs, useSession, useStudioProjects, type StudioProjectListItem } from "@/lib/application";
import { plainText } from "@/lib/application/command-search";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { buildFlow, flowRunStatuses, projectModelId } from "@/lib/application/studio-flow";
import { useActivity, useAiOff, type StudioActivityItem } from "@/lib/application/studio-home-hooks";
import { activityHref, activitySentence, actorLabel, championScoreText, greeting, isAssistantRow, liveRuns, projectHomeHref, recentProjects } from "@/lib/application/studio-home";
import { useInbox, useInboxCounts, useRunNamer, type StudioInboxItem } from "@/lib/application/studio-inbox-hooks";
import { inboxTitle, lookup } from "@/lib/application/studio-inbox";
import { modelName } from "@/lib/application/studio-names";
import { statusWords } from "@/lib/application/studio-runs";
import { isUuid } from "@/lib/application/command-search";
import { safeInternalHref } from "@/components/studio/safe-href";
import { displayName } from "@/lib/infrastructure/session";

const HOME_PROJECTS = 8;
const INBOX_PREVIEW = 5;

function ProjectRow({ project }: { project: StudioProjectListItem }) {
  const href = projectHomeHref(project);
  const goal = project.summary?.goal;
  const score = championScoreText(project);
  const champion = project.summary?.champion;
  const run = project.summary?.latest_run;
  return (
    <tr>
      <td>{href ? <Link href={href}>{plainText(project.name, 80)}</Link> : plainText(project.name, 80)}</td>
      <td>{goal ? <>{goal.target_column ? <span className="mono">{plainText(goal.target_column, 40)}</span> : null}{goal.objective ? <span className="muted"> {plainText(goal.objective, 120)}</span> : null}{!goal.target_column && !goal.objective ? <span className="muted">Goal set, no target yet</span> : null}</> : <span className="muted">No goal yet</span>}</td>
      <td>{score ? <>{score} <span className="muted">(cross-validation)</span></> : <span className="muted">Not available yet</span>}</td>
      <td>{champion ? modelName(plainText(champion.version, 40)) : <span className="muted">None yet</span>}</td>
      <td>{run ? <Pill tone={lookup(STATUS_TONE, run.status, "gray")}>{statusWords(run.status)}</Pill> : <span className="muted">No runs yet</span>}</td>
      <td>{formatWhen(project.updated_at)}</td>
    </tr>
  );
}

function NeedsYouRow({ item }: { item: StudioInboxItem }) {
  const runName = useRunNamer(item.project_id);
  const run = item.subject.kind === "experiment" && isUuid(item.subject.id) ? runName(item.subject.id) : null;
  return <li>{inboxTitle(item, run)} <small className="muted">{formatWhen(item.occurred_at)}</small></li>;
}

function ActivityRow({ item, projectName }: { item: StudioActivityItem; projectName: string | null }) {
  const runName = useRunNamer(item.project_id);
  const run = item.link.kind === "experiment" && isUuid(item.link.id) ? runName(item.link.id) : null;
  const href = activityHref(item);
  const actor = actorLabel(item.actor);
  const sentence = activitySentence(item, run);
  return (
    <li>
      <Pill tone={actor.tone}>{actor.text}</Pill>{" "}
      {href ? <Link href={href}>{sentence}</Link> : sentence}
      {projectName ? <span className="muted"> · {projectName}</span> : null}{" "}
      <small className="muted">{formatWhen(item.occurred_at)}</small>
    </li>
  );
}

/** The most recently changed project as its steps: done and next only where a read says so, blank otherwise. */
function ActiveProject({ project }: { project: StudioProjectListItem }) {
  const refs = useProjectRefs(project.id);
  const runs = useProjectExperiments(project.id);
  const graph = useProjectGraph(project.id);
  const items = refs.data?.items ?? [];
  const targetOf = (kind: string) => items.find((ref) => ref.ref_kind === kind)?.target.id ?? null;
  const steps = buildFlow({
    projectId: project.id, pathname: "/home", available: FLOW_AVAILABLE,
    refKinds: refs.data?.refs_initialized ? items.map((ref) => ref.ref_kind) : refs.data ? [] : null,
    runStatuses: flowRunStatuses(runs.data?.items, !!runs.data?.next_cursor),
    targets: { split_plan: targetOf("split_plan"), project_model: targetOf("champion_model") ?? projectModelId(graph.data?.nodes) },
  });
  const goal = project.summary?.goal;
  return (
    <>
      <p><b>{plainText(project.name, 80)}</b>{goal?.objective ? <span className="muted"> · {plainText(goal.objective, 120)}</span> : null}</p>
      <FlowBar steps={steps} label="Steps of the active project" />
      <p><Link className="btn" href={projectHomeHref(project) ?? "/projects"}>Open project</Link></p>
    </>
  );
}

export default function StudioHomePage() {
  const session = useSession();
  const projects = useStudioProjects();
  const counts = useInboxCounts();
  const inbox = useInbox("needs_decision", INBOX_PREVIEW);
  const activity = useActivity();
  const aiOff = useAiOff();

  const list = projects.data ?? [];
  const recent = recentProjects(list, HOME_PROJECTS);
  const live = liveRuns(list);
  const names = new Map(list.map((p) => [p.id, plainText(p.name, 60)]));
  const feed = (activity.data?.pages.flatMap((page) => page.items) ?? []).filter((item) => !(aiOff === true && isAssistantRow(item)));
  const preview = inbox.data?.pages[0]?.items.slice(0, INBOX_PREVIEW) ?? [];
  const waiting = counts.data?.needs_decision;
  const first = session.user ? displayName(session.user).split(/\s+/)[0] : null;
  const hello = `${greeting(new Date().getHours())}${first ? `, ${plainText(first, 40)}` : ""}.`;

  return (
    <>
      <PageHead
        eyebrow="Workspace" title="Home"
        subtitle={`${hello} ${waiting === undefined ? "" : waiting === 0 ? "Nothing needs you right now." : `${waiting} ${waiting === 1 ? "thing needs" : "things need"} you.`}`.trim()}
        actions={<><Link className="btn primary" href="/projects/new">New project</Link><Link className="btn" href="/projects">All projects</Link></>}
      />
      <PageGuide
        purpose="A starting point for your workspace: what needs you, where your project stands and what happened lately."
        howTo={<>Start with what needs you, then open your project or the Inbox. Press ⌘K (Ctrl+K) to jump to a project, run or model by name. Every value here is read from your workspace; nothing is estimated.</>}
        youGet="The decisions waiting for you, your latest project as its steps, runs in progress, recent activity as sentences, and all your projects."
        attention={waiting ? `${waiting} ${waiting === 1 ? "thing is" : "things are"} waiting for your decision in the Inbox.` : undefined}
      />
      {aiOff === true ? <Banner tone="info">AI suggestions are switched off. Everything on this page works with the rules alone.</Banner> : null}

      <div className="grid cols-2">
        <Card title={waiting === undefined ? "What needs you" : `${waiting} ${waiting === 1 ? "thing needs" : "things need"} you`} aside={<Link href="/inbox">Open the Inbox</Link>}>
          {counts.isError ? <Banner tone="warn">Could not load how many things need you. The list below may still be right; open the Inbox to check.</Banner> : null}
          {inbox.isError ? <QueryNotice error={inbox.error} what="Inbox" /> : null}
          {inbox.isPending ? <p role="status">Loading the Inbox…</p> : null}
          {inbox.data && preview.length === 0 ? <div className="empty">Nothing is waiting for you.</div> : null}
          {preview.length > 0 ? <ul className="plain-list" aria-label="Waiting for you">{preview.map((item) => <NeedsYouRow key={item.id} item={item} />)}</ul> : null}
        </Card>
        <Card title="Your latest project">
          {projects.isError ? <QueryNotice error={projects.error} what="project list" /> : null}
          {projects.isPending ? <p role="status">Loading projects…</p> : null}
          {projects.data && recent.length === 0 ? <div className="empty"><p><b>Create your first project.</b></p><p>A project holds your data, runs, models and the decisions that changed them.</p><p><Link className="btn primary" href="/projects/new">New project</Link></p></div> : null}
          {recent[0] ? <ActiveProject project={recent[0]} /> : null}
        </Card>
      </div>

      <div className="grid cols-2">
        <Card title="Running now" aside={projects.data ? `${live.length} in progress` : undefined}>
          {projects.isPending ? <p role="status">Loading runs…</p> : null}
          {projects.data && live.length === 0 ? <div className="empty">Nothing is running.</div> : null}
          {live.length > 0 ? (
            <ul className="plain-list" aria-label="Runs in progress">
              {live.map(({ project, status, experimentId }) => {
                const href = isUuid(project.id) && isUuid(experimentId) ? safeInternalHref(`/projects/${project.id}/experiments/${experimentId}`) : projectHomeHref(project);
                return <li key={project.id}>{href ? <Link href={href}>Latest run of {plainText(project.name, 60)}</Link> : <>Latest run of {plainText(project.name, 60)}</>} <Pill tone={lookup(STATUS_TONE, status, "gray")}>{statusWords(status)}</Pill></li>;
              })}
            </ul>
          ) : null}
        </Card>
        <Card title="Recent activity" aside="newest first">
          {activity.isError ? <QueryNotice error={activity.error} what="activity feed" /> : null}
          {activity.isPending ? <p role="status">Loading activity…</p> : null}
          {activity.data && feed.length === 0 ? <div className="empty">Nothing has happened yet. Decisions and runs appear here.</div> : null}
          {feed.length > 0 ? (
            <ul className="plain-list" aria-label="Recent activity">
              {feed.map((item) => <ActivityRow key={item.id} item={item} projectName={item.project_id ? (names.get(item.project_id) ?? null) : null} />)}
            </ul>
          ) : null}
          {activity.hasNextPage ? <p><button type="button" className="btn" disabled={activity.isFetchingNextPage} onClick={() => void activity.fetchNextPage()}>{activity.isFetchingNextPage ? "Loading…" : "Load more"}</button></p> : null}
        </Card>
      </div>

      <Card title="Projects" aside={projects.data ? `${list.length} in this workspace` : undefined}>
        {projects.isError ? <QueryNotice error={projects.error} what="project list" /> : null}
        {recent.length > 0 ? (
          <div className="tbl">
            <table>
              <caption className="sr-only">Projects, most recently changed first</caption>
              <thead><tr><th scope="col">Project</th><th scope="col">Goal</th><th scope="col">Best score</th><th scope="col">Model in use</th><th scope="col">Last run</th><th scope="col">Changed</th></tr></thead>
              <tbody>{recent.map((project) => <ProjectRow key={project.id} project={project} />)}</tbody>
            </table>
          </div>
        ) : null}
        <div className="toolbar"><Link className="btn primary" href="/projects/new">New project</Link><Link className="btn" href="/projects/new">Upload data</Link><Link className="btn" href="/agents">Connect</Link></div>
      </Card>
    </>
  );
}
