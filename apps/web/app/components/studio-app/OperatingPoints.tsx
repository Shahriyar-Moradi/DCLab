"use client";

/**
 * Per-fold and threshold card (P5.2-UI). Every number is a field of `GET /v1/experiments/{id}/operating-points`
 * (out-of-fold training-fold figures); the final evaluation is never recomputed here. "Use this point" records a
 * decision only: scoring keeps the locked threshold. The reason is untrusted text and is rendered as text.
 */
import Link from "next/link";
import { useRef, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { plainText, projectHref } from "@/lib/application/command-search";
import { useWriteInvalidation } from "@/lib/application/studio-compare-hooks";
import {
  BASIS_LABEL, CONSTRAINT_METRICS, OBJECTIVE_GOALS, OPERATING_TERMS, REASON_MAX, SCORING_COPY, buildChoice, chartMode, defaultThreshold, onCurve, chartRows, chartSummary,
  chooseGate, constraintLines, focusedPoint, foldSpreadText, fmtRate, fmtThreshold, intervalText, mapChooseError, operatingView, pointRows, pointSentence,
  reasonProblem, type ChooseOutcome, type Pick, type PointRow,
} from "@/lib/application/studio-operating";
import { chooseOperatingPoint, operatingKey, useOperatingPoints } from "@/lib/application/studio-operating-hooks";
import { useSession } from "@/lib/application/session-provider";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";
import { useQueryClient } from "@tanstack/react-query";

const AXIS = { fill: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 };

function ThresholdChart({ data }: { data: NonNullable<ReturnType<typeof useOperatingPoints>["data"]> }) {
  const rows = chartRows(data);
  const cost = chartMode(data) === "expected_cost";
  const pareto = (data.pareto ?? []).slice(0, 40);
  const lines = constraintLines(data);
  const lockedAt = data.locked?.threshold;
  const chosenAt = data.chosen?.threshold;
  return (
    <div role="img" aria-label={chartSummary(data)} style={{ width: "100%", height: 300 }}>
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        <LineChart data={rows} margin={{ top: 8, right: 16, bottom: 4, left: 0 }}>
          <CartesianGrid stroke="var(--line)" vertical={false} />
          <XAxis dataKey="threshold" type="number" domain={["dataMin", "dataMax"]} tick={AXIS} stroke="var(--line-strong)" tickLine={false} tickFormatter={(v: number) => fmtThreshold(v)} />
          <YAxis tick={AXIS} stroke="var(--line-strong)" tickLine={false} width={44} domain={cost ? ["auto", "auto"] : [0, 1]} />
          <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 9, color: "var(--ink)" }} labelFormatter={(v) => `threshold ${fmtThreshold(Number(v))}`} />
          <Legend wrapperStyle={{ color: "var(--ink)", fontSize: 13 }} />
          {cost ? (
            <Line dataKey="expected_cost" name="Expected cost per row" stroke="var(--accent)" strokeWidth={2.2} dot={false} isAnimationActive={false} />
          ) : (
            <>
              <Line dataKey="precision" name="Precision" stroke="var(--accent)" strokeWidth={2.2} dot={false} isAnimationActive={false} />
              <Line dataKey="recall" name="Recall" stroke="var(--ai)" strokeWidth={2.2} strokeDasharray="4 3" dot={false} isAnimationActive={false} />
            </>
          )}
          {!cost ? lines.map((l) => <ReferenceLine key={l.label} y={l.value} stroke="var(--warn)" strokeDasharray="5 4" label={{ value: l.label, fill: "var(--muted)", fontSize: 11, position: "insideTopRight" }} />) : null}
          {lockedAt != null ? <ReferenceLine x={lockedAt} stroke="var(--muted)" strokeDasharray="2 3" label={{ value: "locked", fill: "var(--muted)", fontSize: 11, position: "insideTop" }} /> : null}
          {chosenAt != null ? <ReferenceLine x={chosenAt} stroke="var(--accent)" strokeWidth={2} label={{ value: "chosen", fill: "var(--ink)", fontSize: 11, position: "insideBottom" }} /> : null}
          {pareto.map((p) => <ReferenceDot key={p.threshold} x={p.threshold} y={cost ? p.expected_cost ?? 0 : p.precision} r={4} fill="var(--accent)" stroke="var(--surface)" />)}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function ChooseForm({ experimentId, data, pick, setPick }: { experimentId: string; data: NonNullable<ReturnType<typeof useOperatingPoints>["data"]>; pick: Pick; setPick: (p: Pick) => void }) {
  const { user } = useSession();
  const gate = chooseGate(data, hasCapability(user, CAPABILITIES.workspaceExecuteMl));
  const client = useQueryClient();
  const invalidate = useWriteInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ChooseOutcome | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [recordedUnread, setRecordedUnread] = useState(false);
  const [done, setDone] = useState<{ decisionId: string; threshold: number } | null>(null);
  const hasCost = !!data.cost_matrix;
  const goals = OBJECTIVE_GOALS.filter((g) => g !== "expected_cost" || hasCost);

  if (!gate.allowed) return <p className="muted" role="note">{gate.reason}</p>;
  const submit = async () => {
    if (flight.current) return;
    const built = buildChoice(pick, reason);
    if ("problem" in built) { setProblem(built.problem); return; }
    flight.current = true;
    setBusy(true); setError(null); setProblem(null); setRecordedUnread(false); setDone(null);
    try {
      const result = await chooseOperatingPoint({ experimentId, body: built.body, key: keys.keyFor(`choose:${experimentId}`, JSON.stringify(built.body)) });
      keys.done(`choose:${experimentId}`);
      setDone({ decisionId: result.decision.id, threshold: result.chosen.threshold });
      setReason("");
      invalidate();
      await client.invalidateQueries({ queryKey: operatingKey(experimentId) });
    } catch (caught) {
      const status = (caught as { status?: unknown } | null)?.status;
      if (typeof status === "number" && status >= 200 && status < 300) {
        // The decision was recorded; only the response could not be read.
        setRecordedUnread(true);
        setReason("");
        invalidate();
        await client.invalidateQueries({ queryKey: operatingKey(experimentId) });
      } else {
        setError(mapChooseError(caught));
      }
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  const live = reasonProblem(reason) === null;
  return (
    <form className="form card" onSubmit={(e) => { e.preventDefault(); void submit(); }} aria-label="Use this operating point">
      <h3>Use this point</h3>
      <p className="muted">{SCORING_COPY}</p>
      <fieldset className="field" disabled={busy}>
        <legend><b>How to pick</b></legend>
        <label><input type="radio" name="how" checked={pick.kind === "threshold"} onChange={() => setPick(pick.kind === "threshold" ? pick : { kind: "threshold", threshold: defaultThreshold(data) })} /> A threshold from the curve</label>
        <label><input type="radio" name="how" checked={pick.kind === "objective"} onChange={() => setPick({ kind: "objective", goal: "f1", metric: "recall", op: ">=", value: "" })} /> An objective, solved on the stored curve</label>
      </fieldset>
      {pick.kind === "threshold" ? (
        <label className="field"><span>Threshold</span>
          <select value={String(pick.threshold)} onChange={(e) => setPick({ kind: "threshold", threshold: Number(e.target.value) })}>
            {(data.points ?? []).map((p) => <option key={p.threshold} value={String(p.threshold)}>{fmtThreshold(p.threshold)} (recall {fmtRate(p.recall)}, precision {fmtRate(p.precision)})</option>)}
          </select>
        </label>
      ) : (
        <>
          <label className="field"><span>Goal</span>
            <select value={pick.goal} onChange={(e) => setPick({ ...pick, goal: e.target.value })}>
              {goals.map((g) => <option key={g} value={g}>{g === "expected_cost" ? "minimise expected cost" : `maximise ${g.replaceAll("_", " ")}`}</option>)}
            </select>
          </label>
          <div className="toolbar">
            <label className="field"><span>Constraint on</span>
              <select value={pick.metric} onChange={(e) => setPick({ ...pick, metric: e.target.value })}>{CONSTRAINT_METRICS.map((m) => <option key={m} value={m}>{m.replaceAll("_", " ")}</option>)}</select>
            </label>
            <label className="field"><span>Must be</span>
              <select value={pick.op} onChange={(e) => setPick({ ...pick, op: e.target.value as ">=" | "<=" })}><option value=">=">at least</option><option value="<=">at most</option></select>
            </label>
            <label className="field"><span>Share (0 to 1, optional)</span>
              <input inputMode="decimal" value={pick.value} maxLength={8} placeholder="for example 0.7" onChange={(e) => setPick({ ...pick, value: e.target.value })} />
            </label>
          </div>
        </>
      )}
      <label className="field"><span>Why (recorded with the decision)</span>
        <textarea value={reason} rows={2} maxLength={REASON_MAX} required onChange={(e) => setReason(e.target.value)} />
      </label>
      {problem ? <Banner tone="warn">{problem}</Banner> : null}
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
      {recordedUnread ? <Banner tone="info">Your choice was recorded, but the response could not be read. Refresh the page to see it. Scoring still uses the locked threshold.</Banner> : null}
      {done ? (
        <Banner tone="info">
          Decision recorded: threshold {fmtThreshold(done.threshold)} is now the chosen point. Scoring still uses the locked threshold.{" "}
          <span>Decision <span className="mono">{done.decisionId.slice(0, 8)}</span>.</span>
        </Banner>
      ) : null}
      <div className="toolbar"><button type="submit" className="btn primary" disabled={busy || !live}>{busy ? "Recording…" : "Record this operating point"}</button></div>
    </form>
  );
}

export function OperatingPointsPanel({ projectId, experimentId }: { projectId: string; experimentId: string }) {
  const read = useOperatingPoints(experimentId);
  const [pick, setPick] = useState<Pick>({ kind: "threshold", threshold: Number.NaN });
  if (read.isError) return <QueryNotice error={read.error} what="operating points" />;
  if (!read.data) return <p role="status">Loading operating points…</p>;
  const data = read.data;
  const view = operatingView(data);
  if (view.state === "empty") return <div className="empty" role="note"><p>{view.text}</p></div>;

  const effectivePick: Pick = pick.kind === "threshold" && !onCurve(data, pick.threshold)
    ? { kind: "threshold", threshold: defaultThreshold(data) }
    : pick;
  const focus = focusedPoint(data, effectivePick.kind === "threshold" ? effectivePick.threshold : null);
  const sentence = pointSentence(focus);
  const decisionsHref = projectHref(projectId, "decisions");
  const cost = chartMode(data) === "expected_cost";
  const columns: Column<PointRow>[] = [
    { key: "threshold", header: "Threshold", numeric: true, sortValue: (r) => r.threshold, render: (r) => <span className="mono">{fmtThreshold(r.threshold)}</span> },
    { key: "tags", header: "Marks", render: (r) => <>{r.tags.map((t) => <Pill key={t} tone={t === "Chosen" ? "ok" : t === "Locked" ? "gray" : "det"}>{t}</Pill>)}</> },
    { key: "precision", header: "Precision", numeric: true, sortValue: (r) => r.point.precision, render: (r) => fmtRate(r.point.precision) },
    { key: "recall", header: "Recall", numeric: true, sortValue: (r) => r.point.recall, render: (r) => fmtRate(r.point.recall) },
    { key: "flagged", header: "Flagged share", numeric: true, sortValue: (r) => r.point.flagged_share, render: (r) => fmtRate(r.point.flagged_share) },
    ...(cost ? [{ key: "cost", header: "Expected cost", numeric: true, render: (r: PointRow) => fmtRate(r.point.expected_cost) }] : []),
    { key: "use", header: "Pick", render: (r) => <button type="button" className="btn" onClick={() => setPick({ kind: "threshold", threshold: r.threshold })}>Select<span className="sr-only"> threshold {fmtThreshold(r.threshold)}</span></button> },
  ];
  const detail = focus.detail;
  const notes = [intervalText("Precision", detail?.precision_interval), intervalText("Recall", detail?.recall_interval), ...foldSpreadText(detail)].filter((x): x is string => !!x);
  const chosen = data.chosen;

  return (
    <div className="stack">
      <p className="muted">All figures {BASIS_LABEL}: each training row is predicted by a model that did not see it. {plainText(data.final_evaluation_note, 500)}</p>
      {chosen ? (
        <Banner tone="info">
          Chosen point: threshold <b>{fmtThreshold(chosen.threshold)}</b> ({chosen.method === "objective" ? "solved from an objective" : "picked directly"}), recorded {formatWhen(chosen.recorded_at)}. Reason: {plainText(chosen.rationale, 600)}{" "}
          {decisionsHref ? <Link href={decisionsHref}>Open the decisions</Link> : null} <span className="muted">(decision <span className="mono">{chosen.decision_id.slice(0, 8)}</span>)</span>
          {chosen.curve_changed ? <> <Pill tone="warn">the stored curve changed since</Pill></> : null}
        </Banner>
      ) : null}
      <ThresholdChart data={data} />
      <p className="muted">Dots mark Pareto points, the dashed vertical line the locked threshold{chosen ? " and the solid line the chosen point" : ""}.</p>
      <div role="status" aria-live="polite">
        <b>{focus.label}.</b> {sentence ? plainText(sentence, 600) : "No description is available for this point."}
        {notes.length ? <ul className="plain-list tight" aria-label="Uncertainty of this point">{notes.map((n) => <li key={n}>{n}</li>)}</ul> : null}
      </div>
      <DataTable
        caption="Operating points: Pareto points, the locked threshold and the chosen threshold, out-of-fold"
        columns={columns}
        rows={pointRows(data)}
        rowKey={(r) => r.key}
        highlightRow={(r) => r.tags.includes("Chosen")}
      />
      <KeyValue items={[
        { key: "basis", label: "Basis", value: <>{(data.points?.length ?? 0)} <Term definition={OPERATING_TERMS.threshold}>thresholds</Term>, {data.rows ?? "n/a"} rows ({data.positives ?? "n/a"} positive), <Term definition={OPERATING_TERMS.oof}>out-of-fold</Term></> },
        { key: "locked", label: "Locked threshold", value: <>{fmtThreshold(data.locked?.threshold)} <span className="muted">used for scoring{data.locked?.source ? ` (from ${plainText(data.locked.source, 40).replaceAll("_", " ")})` : ""}</span></> },
        { key: "tie", label: "Tie-break", value: plainText(data.tie_break, 300) },
      ]} />
      {data.optimism_note ? <p className="muted">{plainText(data.optimism_note, 800)}</p> : null}
      <ChooseForm experimentId={experimentId} data={data} pick={effectivePick} setPick={setPick} />
    </div>
  );
}
