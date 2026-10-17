"use client";

/**
 * D3b: "Target over time", the upload log, "Checked on upload" and "Who decided". Every value comes from the D3a reads;
 * file names, column names, values and reasons are plain text; errors are fixed sentences keyed by code. The final-test
 * months stay a sealed band (no line, point or rate) until the final test has run.
 */
import Link from "next/link";
import { Banner } from "@/components/studio/Banner";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { safeInternalHref } from "@/components/studio/safe-href";
import { isUuid, plainText } from "@/lib/application/command-search";
import { decisionHref } from "@/lib/application/studio-pipeline";
import { inspectorPath } from "@/lib/application/studio-inspect";
import { relativeTime, uploaderText } from "@/lib/application/studio-data";
import {
  CHECK_WORD, DISCLOSED_LABEL, NO_UPLOAD_CHECKS, SEALED_LABEL, churnView, monthLabel, readErrorText, retentionText, uploadCheckRows, uploadLogRows, utcText, whoDecidedRows,
  type ChartPoint, type ChurnTableRow, type UploadCheckRow, type UploadChecksLike, type UploadLogRow, type WhoDecidedRow,
} from "@/lib/application/studio-data-goal";
import { useGoalDecisions, useTargetByPeriod, useUploadAttempts } from "@/lib/application/studio-data-goal-reads";

// --- target over time ----------------------------------------------------------------------------------------

const X_PAD = 3;
/** Percent across the plot, clamped so a band or point never passes either edge. */
const xAt = (i: number, n: number) => (n <= 1 ? 50 : Math.min(100, Math.max(0, X_PAD + (i / (n - 1)) * (100 - 2 * X_PAD))));
const yAt = (v: number, max: number) => 100 - Math.min(1, Math.max(0, v / max)) * 100;
const pctLabel = (v: number) => `${Math.round(v * 100)}%`;

/** Runs of consecutive months with a value: a month without one breaks the line. */
function runs(months: number[], points: ChartPoint[]): Array<Array<[number, number]>> {
  const at = new Map(points.map((p) => [p.month, p.rate]));
  const out: Array<Array<[number, number]>> = [];
  let run: Array<[number, number]> = [];
  months.forEach((m, i) => {
    const v = at.get(m);
    if (v === undefined) { if (run.length) out.push(run); run = []; } else run.push([i, v]);
  });
  if (run.length) out.push(run);
  return out;
}

/** Fixed sentences for a page address that does not name a valid id (the read is never sent, so it would load forever). */
const BAD_DATASET_ID = "This page address does not name a valid data version, so there is nothing to show.";
const BAD_PROJECT_ID = "This page address does not name a valid project, so there is nothing to show.";

export function TargetOverTime({ datasetId }: { datasetId: string }) {
  const read = useTargetByPeriod(datasetId);
  if (!isUuid(datasetId)) return <p>{BAD_DATASET_ID}</p>;
  if (read.isError) return <Banner tone="warn">{readErrorText(read.error, "target over time")}</Banner>;
  if (!read.data) return <p role="status">Loading target over time…</p>;
  const view = churnView(read.data);
  if (view.kind === "sentence") return <p data-testid="churn-sentence">{view.text}</p>;
  const n = view.months.length;
  const step = Math.max(1, Math.ceil(n / 6));
  const ticks = view.months.map((m, i) => ({ m, i })).filter((t) => t.i === 0 || t.i === n - 1 || (t.i % step === 0 && n - 1 - t.i >= step / 2));
  const half = n <= 1 ? 50 : (100 - 2 * X_PAD) / (n - 1) / 2;
  const band = view.band
    ? (() => {
      const a = Math.max(0, xAt(view.band.start - view.months[0], n) - half);
      const b = Math.min(100, xAt(view.band.end - view.months[0], n) + half);
      return { left: a, width: b - a };
    })()
    : null;
  const columns: Column<ChurnTableRow>[] = [
    { key: "month", header: "Month (UTC)", render: (r) => r.month },
    { key: "part", header: "Rows", render: (r) => r.part },
    { key: "rows", header: "Row count", numeric: true, render: (r) => r.rows },
    { key: "rate", header: "Share yes", numeric: true, render: (r) => r.rate },
  ];
  const target = read.data.target_column;
  const targetText = target ? plainText(target, 60) : "";
  return (
    <figure className="v7-mon-chart" data-testid="churn-chart">
      <ul className="v7-mon-legend" aria-label="What the chart shows">
        <li><span className="v7-mon-swatch s1" aria-hidden="true" />Training rows</li>
        {view.disclosed.length ? <li><span className="v7-mon-swatch s2" aria-hidden="true" />{DISCLOSED_LABEL}</li> : null}
        {view.band ? <li data-testid="sealed-band-label"><span className="v7-mon-swatch d3-band-key" aria-hidden="true" />{SEALED_LABEL} · {view.band.rows.toLocaleString("en-GB")} rows</li> : null}
      </ul>
      <div className="v7-mon-plot" role="img" aria-label={view.summary}>
        <div className="v7-mon-y" aria-hidden="true">{[view.max, view.max / 2, 0].map((v, i) => <span key={i}>{pctLabel(v)}</span>)}</div>
        <div className="v7-mon-area" aria-hidden="true">
          {band ? <div className="d3-band" data-testid="sealed-band" style={{ left: `${band.left}%`, width: `${band.width}%` }} /> : null}
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" width="100%" height="100%" focusable="false">
            {[0, 50, 100].map((y) => <line key={y} x1="0" x2="100" y1={y} y2={y} className="v7-mon-grid" vectorEffect="non-scaling-stroke" />)}
            {runs(view.months, view.training).filter((s) => s.length > 1).map((s, k) => (
              <polyline key={`t${k}`} className="v7-mon-line s1" vectorEffect="non-scaling-stroke" fill="none" points={s.map(([i, v]) => `${xAt(i, n)},${yAt(v, view.max)}`).join(" ")} />
            ))}
            {runs(view.months, view.disclosed).filter((s) => s.length > 1).map((s, k) => (
              <polyline key={`d${k}`} className="v7-mon-line s2" vectorEffect="non-scaling-stroke" fill="none" points={s.map(([i, v]) => `${xAt(i, n)},${yAt(v, view.max)}`).join(" ")} />
            ))}
          </svg>
          {view.training.map((p) => (
            <span key={`t${p.month}`} className="v7-mon-dot" style={{ left: `${xAt(p.month - view.months[0], n)}%`, top: `${yAt(p.rate, view.max)}%` }} title={`${monthLabel(p.month)}: ${pctLabel(p.rate)} of ${p.rows.toLocaleString("en-GB")} training rows`} />
          ))}
          {view.disclosed.map((p) => (
            <span key={`d${p.month}`} className="v7-mon-dot s2" style={{ left: `${xAt(p.month - view.months[0], n)}%`, top: `${yAt(p.rate, view.max)}%` }} title={`${monthLabel(p.month)}: ${pctLabel(p.rate)} (${DISCLOSED_LABEL.toLowerCase()})`} />
          ))}
        </div>
        <div className="v7-mon-x" aria-hidden="true">
          {ticks.map((t) => <span key={t.m} className={t.i === 0 && n > 1 ? "first" : t.i === n - 1 && n > 1 ? "last" : undefined} style={{ left: `${xAt(t.i, n)}%` }}>{monthLabel(t.m)}</span>)}
        </div>
      </div>
      <figcaption className="muted">
        Share of rows where {targetText ? <code>{targetText}</code> : "the target"} is the answer treated as yes, per month of the training rows.
        {view.disclosed.length ? " Final-test months, once disclosed, are drawn apart." : ""}
        {" "}{view.notes.join(" ")}
      </figcaption>
      <details>
        <summary>The numbers as a table</summary>
        <DataTable caption={`Share answered yes by month: ${targetText || "the target"}`} columns={columns} rows={view.table} rowKey={(r) => r.key} />
      </details>
    </figure>
  );
}

// --- upload log ---------------------------------------------------------------------------------------------

const OUTCOME_TONE: Record<UploadLogRow["outcome"], PillTone> = { accepted: "ok", rejected: "crit", unknown: "gray" };

export function UploadLog({ projectId }: { projectId: string }) {
  const log = useUploadAttempts(projectId);
  if (!isUuid(projectId)) return <p>{BAD_PROJECT_ID}</p>;
  const first = log.data?.pages[0];
  const rows = uploadLogRows(log.data?.pages.flatMap((p) => p.items) ?? [], uploaderText);
  const columns: Column<UploadLogRow>[] = [
    { key: "file", header: "File", render: (r) => <span className="mono">{r.fileName}</span> },
    { key: "when", header: "When", render: (r) => <time dateTime={r.when} title={utcText(r.when)}>{relativeTime(r.when)}</time> },
    { key: "by", header: "By", render: (r) => <>{r.by}<br /><small className="muted">Through: {r.source}</small></> },
    { key: "size", header: "Size", numeric: true, render: (r) => r.size },
    {
      key: "outcome", header: "Outcome",
      render: (r) => (
        <>
          <Pill tone={OUTCOME_TONE[r.outcome]}>{r.outcomeText}</Pill>
          {r.reason ? <> {r.reason} Nothing was added.</> : null}
        </>
      ),
    },
    {
      key: "version", header: "Data version",
      render: (r) => {
        const href = r.datasetId ? safeInternalHref(inspectorPath(projectId, "dataset_version", r.datasetId) ?? "") : null;
        return href ? <Link href={href}>Open this version<span className="sr-only">: {r.fileName}</span></Link> : <span className="muted">—</span>;
      },
    },
  ];
  return (
    <section aria-labelledby="d3-upload-log" data-testid="upload-log">
      <h3 id="d3-upload-log">Upload log</h3>
      <p className="muted">Uploads to this project since the log started, added or rejected, newest first. Uploads refused before they reached the project (for example a declared size over the limit) are not listed. Rejected files were not kept.</p>
      {log.isError && !log.isFetchNextPageError ? <Banner tone="warn">{readErrorText(log.error, "the upload log")}</Banner> : null}
      {log.isPending && !log.isError ? <p role="status">Loading the upload log…</p> : null}
      {log.data ? (
        <DataTable caption="Upload log" columns={columns} rows={rows} rowKey={(r) => r.key}
          emptyMessage={first && first.retention_days > 0
            ? `No upload attempts are recorded in the last ${first.retention_days} days. Files uploaded before the log started are listed under Data versions above.`
            : "No upload attempts are recorded. Files uploaded before the log started are listed under Data versions above."} />
      ) : null}
      {log.isFetchNextPageError ? <Banner tone="warn">{readErrorText(log.error, "more of the upload log")}</Banner> : null}
      {log.hasNextPage ? (
        <p><button type="button" className="btn" disabled={log.isFetchingNextPage} onClick={() => void log.fetchNextPage()}>{log.isFetchingNextPage ? "Loading…" : "Load more"}</button></p>
      ) : null}
      {log.data ? <p className="muted">{retentionText(first?.retention_days)}</p> : null}
    </section>
  );
}

// --- checked on upload --------------------------------------------------------------------------------------

const CHECK_TONE: Record<UploadCheckRow["result"], PillTone> = { ok: "ok", warn: "warn", unknown: "gray" };

export function UploadChecks({ checks }: { checks: UploadChecksLike | null | undefined }) {
  const columns: Column<UploadCheckRow>[] = [
    { key: "check", header: "Check", render: (r) => r.label },
    { key: "result", header: "Result", render: (r) => <Pill tone={CHECK_TONE[r.result]}>{r.word}</Pill> },
    { key: "found", header: "What we found", render: (r) => r.text },
  ];
  return (
    <section aria-labelledby="d3-checked-upload" data-testid="checked-on-upload">
      <h3 id="d3-checked-upload">Checked on upload</h3>
      {checks ? (
        <>
          <p className="muted">
            Run once on the whole file when it was uploaded ({utcText(checks.ran_at)}), over {checks.row_count.toLocaleString("en-GB")} rows. They do not use the
            target and change nothing; “{CHECK_WORD.warn}” means something to look at, not a failure.
          </p>
          <DataTable caption="Checked on upload" columns={columns} rows={uploadCheckRows(checks)} rowKey={(r) => r.key} />
        </>
      ) : <p>{NO_UPLOAD_CHECKS}</p>}
    </section>
  );
}

// --- who decided --------------------------------------------------------------------------------------------

export function WhoDecided({ projectId }: { projectId: string }) {
  const read = useGoalDecisions(projectId);
  if (!isUuid(projectId)) return <p data-testid="who-decided">{BAD_PROJECT_ID}</p>;
  const columns: Column<WhoDecidedRow>[] = [
    { key: "what", header: "What", render: (r) => r.what },
    { key: "value", header: "Value", render: (r) => (r.value === "—" ? <span className="muted">—</span> : <span className="mono">{r.value}</span>) },
    { key: "suggested", header: "Suggested by", render: (r) => r.suggested },
    { key: "confirmed", header: "Confirmed by", render: (r) => r.confirmed },
    { key: "when", header: "When", render: (r) => r.when },
    {
      key: "why", header: "Why",
      render: (r) => {
        const href = r.decisionId ? decisionHref(projectId, r.decisionId) : null;
        return <>{r.why}{href ? <> <Link href={href}>See in History<span className="sr-only">: {r.what}</span></Link></> : null}</>;
      },
    },
  ];
  return (
    <div data-testid="who-decided">
      {read.isError ? <Banner tone="warn">{readErrorText(read.error, "who decided")}</Banner> : null}
      {read.isPending && !read.isError ? <p role="status">Loading who decided…</p> : null}
      {read.data ? <DataTable caption="Who decided" columns={columns} rows={whoDecidedRows(read.data.items)} rowKey={(r) => r.key} emptyMessage="Nothing about this goal is recorded yet." /> : null}
      <p className="muted">Times are in UTC. Each reason is the confirmer&apos;s own words unless it is marked as a DCLab default.</p>
    </div>
  );
}
