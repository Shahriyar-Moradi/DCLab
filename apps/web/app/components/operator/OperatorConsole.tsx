"use client";

/**
 * Operator console (P8.5-UI): platform health for DCLab platform administrators, from the three operator reads only
 * (`/v1/operator/overview`, `/workspaces`, `/failed-jobs`). Read-only: no control changes anything. Counts, ids, codes
 * and workspace names (customer text, rendered as capped plain text); never customer rows, payloads or free-text
 * failure reasons. Tabs without a read say "Planned". Times are UTC.
 */
import { useRouter, useSearchParams } from "next/navigation";
import type { ReactNode } from "react";
import { CopyButton } from "@/app/components/studio-app/CopyButton";
import { ComingNext } from "@/components/studio/ComingNext";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Stat } from "@/components/studio/Stat";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { COMING_NEXT } from "@/lib/application/coming-next";
import { utcWhen } from "@/lib/application/format-time";
import {
  CAPS_READ_ONLY, JOB_STATUSES, NO_CUSTOMER_ROWS, OPERATOR_TABS, TIMES_UTC, ageText, capsItems, checkLabel, checkName, checkSentence, checkTone,
  codeText, count, failedJobRows, failuresView, jobStatusLabel, jobsPivot, nextPageAction, pageSentence, signInHref, queueSentence, readFailure, readinessView, requiredText,
  QUOTA_COLUMNS, statusTag, tabFromQuery, workersView, workspaceRows, type FailedJobRow, type JobPivotRow, type ReadErrorLike, type WorkspaceRow,
} from "@/lib/application/operator";
import { useOperatorFailedJobs, useOperatorListReset, useOperatorOverview, useOperatorWorkspaces, type OperatorOverview } from "@/lib/application/operator-hooks";

const TABS = "operator";

function ReadProblem({ error, what, onRetry, retryLabel = "Reload" }: { error: unknown; what: string; onRetry: () => void; retryLabel?: string }) {
  const state = readFailure(error as ReadErrorLike, what);
  const tab = tabFromQuery(useSearchParams().get("tab"));
  if (state.kind === "forbidden") return <Banner tone="warn"><span data-testid="operator-forbidden">{state.text}</span></Banner>;
  if (state.kind === "signed_out") return <Banner tone="warn">{state.text} <a href={signInHref(tab)}>Sign in</a>.</Banner>;
  return <Banner tone="crit" actions={<button type="button" className="btn" onClick={onRetry}>{retryLabel}</button>}>{state.text}</Banner>;
}

/** A failed "show more": a refused cursor starts the list over (the query is reset); anything else retries that page. */
function NextPageProblem({ error, what, onRetry, onRestart }: { error: unknown; what: string; onRetry: () => void; onRestart: () => void }) {
  const action = nextPageAction(error as ReadErrorLike);
  return <ReadProblem error={error} what={what} retryLabel={action.label} onRetry={action.restart ? onRestart : onRetry} />;
}

function Planned({ text }: { text: string }) {
  return <div className="empty" role="note"><p>{text}</p></div>;
}

function Overview({ data }: { data: OperatorOverview }) {
  const jobs = jobsPivot(data.jobs_last_24h);
  const failures = failuresView(data.failed_jobs_last_24h, data.failed_jobs_last_24h_truncated);
  const workers = workersView(data.workers);
  const ready = readinessView(data.readiness.status);
  const caps = capsItems(data.platform_ai_caps);
  const jobColumns: Column<JobPivotRow>[] = [
    { key: "type", header: "Job type", render: (r) => <>{r.label} <span className="mono muted">{codeText(r.type)}</span></> },
    ...JOB_STATUSES.map((s) => ({ key: s, header: jobStatusLabel(s), numeric: true, render: (r: JobPivotRow) => count(r.counts[s]) })),
    { key: "other", header: "Other", numeric: true, render: (r) => count(r.other) },
    { key: "total", header: "Total", numeric: true, render: (r) => count(r.total) },
  ];
  return (
    <div className="grid" style={{ gap: 24 }}>
      <p className="muted" data-testid="operator-generated">Counts as of {utcWhen(data.generated_at)}.</p>
      <div className="grid cols-4">
        <Stat value={count(data.workspaces.total)} label="Workspaces" hint={`${count(data.workspaces.active_last_7d)} queued a job in the last 7 days`} />
        <Stat value={count(data.queue.depth)} label="Jobs waiting" hint={data.queue.oldest_age_seconds === null ? "None waiting" : `Oldest waited ${ageText(data.queue.oldest_age_seconds)}`} />
        <Stat value={workers.label} label="Workers" hint={data.workers.dispatcher === "postgres" ? "Separate worker processes" : "Jobs run in the API"} />
        <Stat value={`${data.failed_jobs_last_24h_truncated ? "≥ " : ""}${count(failures.total)}`} label="Failed jobs, last 24 h" hint={data.failed_jobs_last_24h_truncated ? "Lower bound" : "Every failure counted"} />
      </div>
      <Card title="Readiness" aside={<Pill tone={ready.tone}>{ready.label}</Pill>}>
        <p data-testid="readiness-sentence"><b>{ready.sentence}</b></p>
        <ul className="approvals" aria-label="Readiness checks">
          {data.readiness.checks.map((check, i) => (
            <li key={`${i}:${check.name}`}>
              <p><b>{checkName(check.name)}</b> <Pill tone={checkTone(check.status)}>{checkLabel(check.status)}</Pill> <span className="muted">{requiredText(check.required)}</span></p>
              <p>{checkSentence(check)}</p>
            </li>
          ))}
        </ul>
      </Card>
      <div className="grid cols-2">
        <Card title="Queue and workers">
          <p data-testid="queue-sentence">{queueSentence(data.queue)}</p>
          <p data-testid="workers-sentence">{workers.headline}</p>
          {workers.lines.length ? <ul aria-label="Workers, freshest first">{workers.lines.map((line, i) => <li key={i}>{line}</li>)}</ul> : null}
        </Card>
        <Card title="Failures in the last 24 hours">
          <p data-testid="failures-sentence"><b>{failures.sentence}</b></p>
          <p className="muted" data-testid="failures-scope">{failures.scope}</p>
          {failures.items.length ? (
            <ul className="approvals" aria-label="Failed jobs by reason, most first">
              {failures.items.map((item) => <li key={item.key}><p><b className="mono">{item.count}</b> · {item.sentence}</p></li>)}
            </ul>
          ) : null}
        </Card>
      </div>
      <section aria-labelledby="jobs-24h" className="grid" style={{ gap: 12 }}>
        <h2 id="jobs-24h">Jobs in the last 24 hours</h2>
        <p data-testid="jobs-sentence">{jobs.sentence}</p>
        {jobs.rows.length ? <DataTable caption="Jobs queued in the last 24 hours by type and status" columns={jobColumns} rows={jobs.rows} rowKey={(r) => r.type} scrollLabel="Jobs queued in the last 24 hours (scrolls sideways on small screens)" /> : null}
      </section>
      <Card title="Platform AI caps" aside={<Pill tone="det">Read-only</Pill>}>
        <p className="muted">{CAPS_READ_ONLY}</p>
        <KeyValue items={caps.items.map((item) => ({ key: item.key, label: item.label, value: item.value }))} />
        {caps.unknownBudgets ? <p className="muted">{caps.unknownBudgets} more {caps.unknownBudgets === 1 ? "limit is" : "limits are"} in the read but not named on this page yet.</p> : null}
      </Card>
      <ComingNext entry={COMING_NEXT.backups} />
    </div>
  );
}

function IdCell({ id, short, label }: { id: string | null; short: string; label: string }) {
  if (!id) return <span className="muted">—</span>;
  return <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}><span className="mono" title={id}>{short}</span><CopyButton text={id} label={label} /></span>;
}

function MoreButton({ more, loading, onMore, what }: { more: boolean; loading: boolean; onMore: () => void; what: string }) {
  if (!more) return null;
  return <p><button type="button" className="btn" disabled={loading} onClick={onMore}>{loading ? "Loading…" : `Show more ${what}`}</button></p>;
}

function Workspaces() {
  const read = useOperatorWorkspaces(true);
  const restart = useOperatorListReset("workspaces");
  if (read.isPending) return <p role="status">Loading workspaces…</p>;
  if (read.isError && !read.data) return <ReadProblem error={read.error} what="workspaces" onRetry={() => void read.refetch()} />;
  const rows = workspaceRows((read.data?.pages ?? []).flatMap((page) => page.items));
  const columns: Column<WorkspaceRow>[] = [
    { key: "name", header: "Name", render: (r) => <span data-untrusted="" style={{ display: "inline-block", minWidth: "14ch", overflowWrap: "break-word" }}>{r.name}</span> },
    { key: "kind", header: "Kind" },
    { key: "created", header: "Created (UTC)" },
    { key: "members", header: "Members", numeric: true, render: (r) => count(r.members) },
    { key: "projects", header: "Projects", numeric: true, render: (r) => count(r.projects) },
    { key: "jobs30d", header: "Jobs, 30 days", numeric: true, render: (r) => count(r.jobs30d) },
    { key: "failed24h", header: "Failed, 24 h", numeric: true, render: (r) => count(r.failed24h) },
    { key: "active", header: "Active, 7 days" },
    { key: "plan", header: "Plan", render: (r) => <span data-untrusted="">{r.plan}</span> },
    ...QUOTA_COLUMNS.map((q) => ({
      key: q.key, header: q.header,
      render: (r: WorkspaceRow) => {
        const cell = q.key === "compute_seconds_month" ? r.compute : q.key === "storage_bytes" ? r.storage : r.runs;
        return <>{cell.text}{cell.over ? <> <Pill tone="crit">at or over the limit</Pill></> : null}</>;
      },
    })),
    { key: "beta", header: "Assistant beta", render: (r) => <Pill tone={r.beta === "On" ? "ai" : "gray"}>{r.beta}</Pill> },
    { key: "id", header: "Workspace id", render: (r) => <IdCell id={r.id} short={r.shortId} label={`Copy the id of workspace ${r.name}`} /> },
  ];
  return (
    <div className="grid" style={{ gap: 16 }}>
      <p>Every workspace, oldest first. Names are chosen by customers and shown as plain text. &ldquo;Active&rdquo; means it queued at least one job in the last 7 days. {TIMES_UTC}</p>
      <p className="muted">Plan and usage are what the workspace&apos;s own Usage page measures: compute this month, storage now and runs today, each against its plan limit (&ldquo;unlimited&rdquo; when the plan sets none). &ldquo;At or over the limit&rdquo; means a new run or upload would be refused while limits are enforced. The assistant beta is switched per workspace by DCLab staff on the command line.</p>
      {read.isError ? <NextPageProblem error={read.error} what="workspaces" onRetry={() => void read.fetchNextPage()} onRestart={restart} /> : null}
      <DataTable caption="Workspaces, oldest first" columns={columns} rows={rows} rowKey={(r) => r.key} emptyMessage="There is no workspace yet." />
      {rows.length ? <p className="muted" role="status">{pageSentence(rows.length, !!read.hasNextPage, ["workspace", "workspaces"])}</p> : null}
      <MoreButton more={!!read.hasNextPage} loading={read.isFetchingNextPage} onMore={() => void read.fetchNextPage()} what="workspaces" />
    </div>
  );
}

function FailedJobs() {
  const read = useOperatorFailedJobs(true);
  const restart = useOperatorListReset("failed-jobs");
  if (read.isPending) return <p role="status">Loading failed jobs…</p>;
  if (read.isError && !read.data) return <ReadProblem error={read.error} what="failed jobs" onRetry={() => void read.refetch()} />;
  const rows = failedJobRows((read.data?.pages ?? []).flatMap((page) => page.items));
  const columns: Column<FailedJobRow>[] = [
    { key: "when", header: "Failed (UTC)" },
    { key: "type", header: "Job type", render: (r) => <>{r.type} <span className="mono muted">{r.typeCode}</span></> },
    { key: "handler", header: "Handler", render: (r) => <span className="mono" style={{ overflowWrap: "anywhere" }}>{r.handler}</span> },
    { key: "sentence", header: "What happened" },
    { key: "attempts", header: "Attempts", numeric: true, render: (r) => count(r.attempts) },
    { key: "id", header: "Job id", render: (r) => <IdCell id={r.id} short={r.shortId} label={`Copy the job id ${r.shortId}`} /> },
    { key: "workspace", header: "Workspace id", render: (r) => <IdCell id={r.workspaceId} short={r.workspaceShort} label={`Copy the workspace id of job ${r.shortId}`} /> },
  ];
  return (
    <div className="grid" style={{ gap: 16 }}>
      <p>Failed jobs, newest first, across every workspace. Each reason is a fixed code turned into a sentence; free-text reasons can hold customer values, so they stay in the worker log. {TIMES_UTC}</p>
      {read.isError ? <NextPageProblem error={read.error} what="failed jobs" onRetry={() => void read.fetchNextPage()} onRestart={restart} /> : null}
      <DataTable caption="Failed jobs, newest first" columns={columns} rows={rows} rowKey={(r) => r.key} emptyMessage="No job has failed." />
      {rows.length ? <p className="muted" role="status">{pageSentence(rows.length, !!read.hasNextPage, ["failed job", "failed jobs"])}</p> : null}
      <MoreButton more={!!read.hasNextPage} loading={read.isFetchingNextPage} onMore={() => void read.fetchNextPage()} what="failed jobs" />
    </div>
  );
}

export function OperatorConsole() {
  const router = useRouter();
  const tab = tabFromQuery(useSearchParams().get("tab"));
  const overview = useOperatorOverview(true);
  const forbidden = overview.isError && readFailure(overview.error as ReadErrorLike, "overview").kind === "forbidden";
  const choose = (id: string) => router.replace(id === "overview" ? "/operator" : `/operator?tab=${encodeURIComponent(tabFromQuery(id))}`, { scroll: false });

  let panel: ReactNode = null;
  if (tab === "overview") {
    panel = overview.isPending ? <p role="status">Loading the overview…</p>
      : overview.isError || !overview.data ? <ReadProblem error={overview.error} what="overview" onRetry={() => void overview.refetch()} />
        : <Overview data={overview.data} />;
  } else if (tab === "workspaces") panel = <Workspaces />;
  else if (tab === "failed-jobs") panel = <FailedJobs />;
  else panel = <Planned text={OPERATOR_TABS.find((t) => t.id === tab)?.planned ?? ""} />;

  return (
    <>
      <PageHead
        title="Operator console"
        subtitle="Platform health across every workspace: jobs, queue, workers, failures, readiness and the platform AI caps."
      />
      <PageGuide
        purpose="The start page for DCLab platform administrators: whether the platform is ready, whether jobs are waiting or failing, and which workspaces exist."
        howTo="Read Readiness first. If jobs are waiting with no worker, or failures climb, open Failed jobs to see the reason codes and copy the ids for the logs."
        youGet="Counts for the last 24 hours, the queue and worker heartbeats, failures by reason, readiness checks in plain words, the workspace list and the failed job list."
        attention={<>{NO_CUSTOMER_ROWS} It is read-only: nothing here changes a workspace, a job or a cap.</>}
      />
      {forbidden ? <ReadProblem error={overview.error} what="overview" onRetry={() => void overview.refetch()} /> : (
        <>
          <Tabs
            idPrefix={TABS} label="Operator console sections" value={tab} onChange={choose}
            items={OPERATOR_TABS.map((t) => ({ id: t.id, label: t.label }))}
          />
          {OPERATOR_TABS.map((t) => (
            <TabPanel key={t.id} idPrefix={TABS} id={t.id} active={t.id === tab}>
              {t.id === tab ? (
                <div className="grid" style={{ gap: 16, paddingTop: 16 }}>
                  <p><Pill tone={statusTag(t.status).tone}>{statusTag(t.status).label}</Pill></p>
                  {panel}
                </div>
              ) : null}
            </TabPanel>
          ))}
        </>
      )}
    </>
  );
}
