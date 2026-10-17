"use client";

/**
 * Monitoring trends and steps (V7 UI-E): input change (PSI) by week for one column beside the largest change of any column,
 * real precision and recall by week once outcomes are measured, and "What the system does on its own" with the live state
 * of each step (weekly checks, the alert rule, retrain on newest data, the comparison, switch or keep, going back). Every
 * value comes from the monitoring overview read; column names are untrusted plain text; no final test value is shown.
 */
import { useId, useState, type ReactNode } from "react";
import { Card } from "@/components/studio/Card";
import { Pill } from "@/components/studio/Pill";
import {
  REAL_TREND_NOTE, THRESHOLDS_READ_ONLY, ownSteps, trendCell, psiTrend, realTrend, realTrendEmpty, seriesColumns,
  type FeatureSeriesLike, type GoBack, type OverviewLike, type OwnStep, type Thresholds, type TrendChart, type TrendsWeek,
} from "@/lib/application/studio-monitoring";

const X_PAD = 3;
const xAt = (i: number, n: number) => (n <= 1 ? 50 : X_PAD + (i / (n - 1)) * (100 - 2 * X_PAD));
const yAt = (v: number, max: number) => 100 - Math.min(1, Math.max(0, v / max)) * 100;

/** Runs of consecutive values: a missing week breaks the line instead of being drawn as zero. */
function segments(values: ReadonlyArray<number | null>): Array<Array<[number, number]>> {
  const out: Array<Array<[number, number]>> = [];
  let run: Array<[number, number]> = [];
  values.forEach((v, i) => {
    if (v === null) { if (run.length) out.push(run); run = []; } else run.push([i, v]);
  });
  if (run.length) out.push(run);
  return out;
}

/** One line chart over weeks, with a legend, the reference lines, a caption and the numbers as a table. */
export function WeekTrendChart({ chart, caption, testId }: { chart: TrendChart; caption: string; testId: string }) {
  const n = chart.weeks.length;
  const step = Math.max(1, Math.ceil(n / 6));
  const ticks = chart.weeks.map((w, i) => ({ ...w, i })).filter((w) => w.i === 0 || w.i === n - 1 || (w.i % step === 0 && n - 1 - w.i >= step / 2));
  const yLabels = [chart.max, chart.max / 2, 0];
  return (
    <figure className="v7-mon-chart" data-testid={testId}>
      <ul className="v7-mon-legend" aria-label="Lines in the chart">
        {chart.lines.map((l) => <li key={l.id}><span className={`v7-mon-swatch ${l.style}`} aria-hidden="true" />{l.label}</li>)}
        {chart.refs.map((r) => <li key={r.label}><span className={`v7-mon-swatch ref-${r.tone}`} aria-hidden="true" />{r.label} (in force now)</li>)}
      </ul>
      <div className="v7-mon-plot" role="img" aria-label={chart.summary}>
        <div className="v7-mon-y" aria-hidden="true">{yLabels.map((v, i) => <span key={i}>{v.toFixed(2)}</span>)}</div>
        <div className="v7-mon-area" aria-hidden="true">
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" width="100%" height="100%" focusable="false">
            {[0, 50, 100].map((y) => <line key={y} x1="0" x2="100" y1={y} y2={y} className="v7-mon-grid" vectorEffect="non-scaling-stroke" />)}
            {chart.refs.map((r) => <line key={r.label} x1="0" x2="100" y1={yAt(r.value, chart.max)} y2={yAt(r.value, chart.max)} className={`v7-mon-refline ${r.tone}`} vectorEffect="non-scaling-stroke" />)}
            {chart.lines.flatMap((l) => segments(l.values).filter((s) => s.length > 1).map((s, k) => (
              <polyline key={`${l.id}-${k}`} className={`v7-mon-line ${l.style}`} vectorEffect="non-scaling-stroke" fill="none"
                points={s.map(([i, v]) => `${xAt(i, n)},${yAt(v, chart.max)}`).join(" ")} />
            )))}
          </svg>
          {chart.refs.map((r) => <span key={r.label} className={`v7-mon-reflabel ${r.tone}`} style={{ top: `${yAt(r.value, chart.max)}%` }}>{r.label}</span>)}
          {chart.lines.flatMap((l) => l.values.map((v, i) => (v === null ? null : (
            <span key={`${l.id}-${i}`} className={`v7-mon-dot ${l.style}${v > chart.max ? " clipped" : ""}${l.marks?.[i] === "chance" ? " hollow" : ""}`} style={{ left: `${xAt(i, n)}%`, top: `${yAt(v, chart.max)}%` }}
              title={`${l.label}, week of ${chart.weeks[i].label}: ${trendCell(l, i)}`} />
          ))))}
        </div>
        <div className="v7-mon-x" aria-hidden="true">
          {ticks.map((w) => <span key={w.key} className={w.i === 0 && n > 1 ? "first" : w.i === n - 1 && n > 1 ? "last" : undefined} style={{ left: `${xAt(w.i, n)}%` }}>{w.label}</span>)}
        </div>
      </div>
      <figcaption className="muted">{caption}{chart.note ? ` ${chart.note}` : ""}</figcaption>
      <details>
        <summary>The numbers as a table</summary>
        <div className="tbl">
          <table>
            <caption className="sr-only">{caption}</caption>
            <thead><tr>{chart.headers.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
            <tbody>{chart.rows.map((r, i) => <tr key={chart.weeks[i]?.key ?? i}>{r.map((c, j) => (j === 0 ? <th key={j} scope="row">{c}</th> : <td key={j} className="num">{c}</td>))}</tr>)}</tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}

/** Input change by week (one column at a time) and real precision / recall by week. */
export function TrendsSection({ windows, series, thresholds, fallback }: { windows: readonly TrendsWeek[]; series: readonly FeatureSeriesLike[] | undefined; thresholds: Thresholds; fallback?: Pick<Thresholds, "psi_watch" | "psi_alert"> }) {
  const selectId = useId();
  const columns = seriesColumns(series);
  const [column, setColumn] = useState<number | null>(null);
  const chosen = columns.some((c) => c.index === column) ? column : columns[0]?.index ?? null;
  const psi = psiTrend(windows, series, chosen, thresholds, fallback ?? thresholds);
  const empty = realTrendEmpty(windows);
  return (
    <>
      <Card title="Input change by week">
        <p>How much one column&apos;s values moved away from training each week, beside the largest change of any column. A line that climbs toward the alert line is worth a look before it gets there.</p>
        {columns.length > 1 ? (
          <div className="field v7-mon-pick">
            <label htmlFor={selectId}>Column</label>
            <select id={selectId} value={chosen ?? ""} onChange={(e) => setColumn(Number(e.target.value))}>
              {columns.map((c) => <option key={c.index} value={c.index}>{c.name}</option>)}
            </select>
          </div>
        ) : null}
        <WeekTrendChart chart={psi} testId="psi-trend" caption={`Weeks are Monday to Monday, UTC, oldest on the left. The columns that moved most have a weekly line; date columns are never judged. A gap means the column was not among that week's shown columns. Crossing a line counts only when the statistical test agrees. ${THRESHOLDS_READ_ONLY}`} />
      </Card>
      <Card title="Real precision and recall by week">
        {empty ? <p data-testid="real-trend-empty">{empty}</p> : <WeekTrendChart chart={realTrend(windows)} testId="real-trend" caption={REAL_TREND_NOTE} />}
        <p className="muted">These are real outcomes of rows the model scored, not the final test. They are never used to train or to choose a model.</p>
      </Card>
    </>
  );
}


/** "What the system does on its own": the live state of each step; `retrain` are the retrain steps, `children` their action. */
export function OwnStepsCard({ overview, offer, retrain, children }: { overview: OverviewLike | undefined; offer: GoBack; retrain: readonly OwnStep[]; children?: ReactNode }) {
  const steps = ownSteps(overview, offer, retrain);
  return (
    <Card title="What the system does on its own" aside={<span className="muted">and where it stops for you</span>}>
      <ol className="v7-mon-steps" data-testid="own-steps">
        {steps.map((s) => (
          <li key={s.key} data-state={s.state}>
            <p><b>{s.title}</b> <span className="muted">· {s.who}</span> <Pill tone={s.tone}>{s.pill}</Pill></p>
            <p>{s.text}</p>
          </li>
        ))}
      </ol>
      {children}
      <p className="muted">Nothing on this screen changes the model in use by itself. Switching models, or going back, is always done by a person and saved in History.</p>
    </Card>
  );
}
