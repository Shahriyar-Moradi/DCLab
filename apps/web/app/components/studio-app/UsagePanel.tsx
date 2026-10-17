"use client";

import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { Pill } from "@/components/studio/Pill";
import { useSession } from "@/lib/application";
import {
  USAGE_DISCLAIMER_FALLBACK,
  USAGE_FORBIDDEN_TEXT,
  USAGE_HONEST_NOTES,
  aiSpendView,
  dailySeries,
  formatUtcDate,
  freshnessText,
  noteTexts,
  quotaBars,
  usageScreenState,
} from "@/lib/application/studio-usage";
import { isUuid, plainText } from "@/lib/application/command-search";
import { useWorkspaceUsage } from "@/lib/application/studio-settings-hooks";
import { ApiError } from "@/lib/infrastructure/api-client";

const TONE = { ok: "ok", warning: "warn", over: "crit", unlimited: "gray", none: "gray" } as const;

/** Settings -> Usage & cost: a read of GET /v1/workspaces/{id}/usage. No buttons: plans are set by DCLab staff. */
export function UsagePanel() {
  const session = useSession();
  const usage = useWorkspaceUsage(session.activeWorkspaceId, session.loaded);
  const status = usage.error instanceof ApiError ? usage.error.status : null;
  const hasWorkspace = isUuid(session.activeWorkspaceId);
  const state = usageScreenState({ loaded: session.loaded, hasWorkspace, pending: usage.isPending, status, failed: usage.isError, data: usage.data });
  const data = usage.data;

  if (state === "loading") return <p role="status">Loading usage and limits…</p>;
  if (state === "no_workspace") return <Card title="Usage and cost"><Banner tone="info">Choose a workspace to see its usage and limits.</Banner></Card>;
  if (state === "forbidden") return <Card title="Usage and cost"><Banner tone="info">{USAGE_FORBIDDEN_TEXT}</Banner></Card>;
  if (state === "error") return <Card title="Usage and cost"><Banner tone="crit">Could not load usage and limits. Nothing is shown rather than a wrong number. Try again in a moment.</Banner></Card>;
  if (state === "empty" || !data) return <Card title="Usage and cost"><div className="empty">No usage has been recorded for this workspace yet.</div></Card>;

  const bars = quotaBars(data.quotas);
  const ai = aiSpendView(data.ai_spend);
  const series = dailySeries(data.daily);
  const notes = noteTexts(data.notes);
  const disclaimer = plainText(data.disclaimer, 300) || USAGE_DISCLAIMER_FALLBACK;

  return (
    <>
      <Card title="Your plan" aside={<Pill tone="det">{plainText(data.plan, 40) || "Plan unknown"}</Pill>}>
        <p>Monthly limits start again on {formatUtcDate(data.resets_at)}.</p>
        {!data.quotas_enforced ? <Banner tone="info">Limits are shown but are not refusing work right now.</Banner> : null}
        <p className="small muted">{disclaimer}</p>
      </Card>

      <Card title="Limits">
        {bars.length === 0 ? <div className="empty">No limits are set for this plan.</div> : (
          <ul className="usage-bars" aria-label="Limits">
            {bars.map((bar) => (
              <li key={bar.key}>
                <div className="usage-row">
                  <strong>{bar.label}</strong>
                  <span><Pill tone={TONE[bar.state]}>{bar.stateText}</Pill></span>
                </div>
                <div
                  className={`meter ${bar.state === "over" ? "over" : bar.state === "warning" ? "warning" : ""}`}
                  role="img"
                  aria-label={`${bar.label}: ${bar.usedText} of ${bar.limitText}${bar.percentText ? `, ${bar.percentText}` : ""}. ${bar.stateText}.`}
                >
                  <i style={{ width: `${bar.width}%` }} />
                </div>
                <div className="usage-row small muted">
                  <span>{bar.state === "unlimited" ? `${bar.usedText} used` : bar.noAllowance ? `${bar.usedText} used; ${bar.limitText}` : `${bar.usedText} of ${bar.limitText}${bar.percentText ? ` (${bar.percentText})` : ""}`}</span>
                  <span>{bar.resetText}</span>
                </div>
                {bar.enforcedNote ? <p className="small muted">{bar.enforcedNote}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="AI spend this month">
        {ai ? (
          <>
            <div className="usage-row">
              <strong>{ai.state === "none" ? `${ai.spentText} spent. ${ai.limitText}.` : `${ai.spentText} of ${ai.limitText} (${ai.percentText})`}</strong>
              <span><Pill tone={TONE[ai.state]}>{ai.stateText}</Pill></span>
            </div>
            <div className={`meter ${ai.state === "over" ? "over" : ai.state === "warning" ? "warning" : ""}`} role="img" aria-label={`AI spend: ${ai.spentText} of ${ai.limitText}. ${ai.stateText}.`}>
              <i style={{ width: `${ai.width}%` }} />
            </div>
            <p className="small muted">{ai.reservedText} is set aside for AI work in progress. {ai.hardStopText} This is the workspace&apos;s existing AI budget.</p>
          </>
        ) : <div className="empty">The AI budget cannot be read for this workspace, so no figure is shown.</div>}
      </Card>

      <Card title="Compute time, last 31 days" aside={<span className="small muted">UTC days</span>}>
        <p className="small muted">{freshnessText(data.measured_at)} The limit bars above are measured live.</p>
        {!data.metering_enabled ? <Banner tone="info">Daily totals are not being recorded on this platform right now.</Banner> : null}
        {series.bars.length === 0 ? <div className="empty">No days to show.</div> : (
          <>
            <svg className="usage-chart" viewBox="0 0 310 90" role="img" aria-label={series.summary}>
              {series.bars.map((bar, i) => (
                <g key={bar.day}>
                  <title>{`${bar.day}: ${bar.text}`}</title>
                  <rect x={i * 10 + 1} y={80 - bar.height * 0.75} width={8} height={bar.height * 0.75} rx={1} />
                </g>
              ))}
              <line x1={0} x2={310} y1={80.5} y2={80.5} stroke="var(--line)" />
              <text x={0} y={90}>{series.bars[0]?.label}</text>
              <text x={310} y={90} textAnchor="end">{series.bars[series.bars.length - 1]?.label}</text>
            </svg>
            {series.allZero ? <p className="small muted">No compute time was recorded in these days.</p> : <p className="small muted">{series.summary}</p>}
            <details>
              <summary>Show the numbers as a table</summary>
              <div className="tbl">
                <table>
                  <caption className="sr-only">Compute time per day, last 31 days (UTC)</caption>
                  <thead><tr><th scope="col">Day (UTC)</th><th scope="col">Compute time</th></tr></thead>
                  <tbody>{series.bars.map((bar) => <tr key={bar.day}><th scope="row">{bar.day}</th><td>{bar.text}</td></tr>)}</tbody>
                </table>
              </div>
            </details>
          </>
        )}
      </Card>

      <Card title="How to read this">
        <ul className="plain-list">
          {USAGE_HONEST_NOTES.map((text) => <li key={text}>{text}</li>)}
          {notes.map((text) => <li key={text}>{text}</li>)}
        </ul>
        <p className="small muted">{disclaimer}</p>
      </Card>
    </>
  );
}
