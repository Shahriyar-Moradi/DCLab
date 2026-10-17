"use client";

/**
 * One improvement loop (D4e = P5.5-A): where it stands, what it spent, why it stopped, each sealed try with its fold-by-fold verdict,
 * and, when it waits for you, accept one try (a person, with a reason) or keep the current model. Accept and cancel send the read's
 * ETag in If-Match and an Idempotency-Key; the buttons follow the read's `actions` (the server still decides). After an accepted try
 * the final run's progress is shown and then its single final-test number, labelled as such. Untrusted text is rendered as text.
 */
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { formatWhen } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { Chart } from "@/components/studio/Chart";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { StepBar } from "@/components/studio/StepBar";
import { Term } from "@/components/studio/Term";
import { isUuid, projectHref } from "@/lib/application/command-search";
import { fmtScore } from "@/lib/application/studio-improve";
import {
  ONE_LOOK_NOTE, READ_TEXT, REASON_MAX, SEALED_NOTE, WITHIN_NOISE_NOTE,
  acceptBody, acceptGate, againstName, capitalize, chartRows, isStale, finalTestView, foldRows, foldSummary, goalWords, loopError, loopStatus, loopSteps, outcomeSentence, readState,
  sealedSetText, spendView, stopDetailText, stopReasonText, tryBadge, tryChange, tryName, trySentence, tryVerdict, type FoldRow, type LoopIteration,
} from "@/lib/application/studio-improve-loop";
import { acceptImproveLoop, cancelImproveLoop, useFinalRun, useImproveLoop, useLoopInvalidation, type StudioLoop } from "@/lib/application/studio-improve-loop-hooks";
import { statusWords } from "@/lib/application/studio-runs";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

const NOISE_TERM = "How much two scores can differ just by chance on these folds, allowing for the folds not being independent and for the number of tries the loop planned. A gain bigger than the margin is unlikely to be luck. It is evidence, not proof.";

function Folds({ rows, name, against }: { rows: FoldRow[]; name: string; against: string }) {
  const columns: Column<FoldRow>[] = [
    { key: "fold", header: "Fold", numeric: true, render: (r) => r.fold },
    { key: "current", header: capitalize(against), numeric: true, render: (r) => r.current },
    { key: "try", header: "This try", numeric: true, render: (r) => r.tried },
    { key: "diff", header: "Difference (this try − the other)", numeric: true, render: (r) => r.difference },
  ];
  return <DataTable caption={`Fold by fold goal scores of ${name}`} columns={columns} rows={rows} rowKey={(r) => String(r.fold)} />;
}

function TryItem({ it, run, projectId }: { it: LoopIteration; run: StudioLoop; projectId: string }) {
  const key = tryVerdict(it);
  const against = againstName(it, run);
  const badge = tryBadge(key);
  const name = tryName(it);
  const summary = foldSummary(it.comparison);
  const rows = foldRows(it.comparison);
  const runHref = it.kind === "family_include" && it.experiment_id ? projectHref(projectId, "experiments", it.experiment_id) : null;
  return (
    <li className="card flat">
      <h3>{name} · {tryChange(it)} <Pill tone={badge.tone}>{badge.label}</Pill> <Pill tone="det">Final test not used</Pill></h3>
      <p>{trySentence(it, against)}</p>
      {summary && !summary.byShortfall ? (
        <KeyValue items={[
          { key: "try", label: "This try (mean over folds)", value: <>{fmtScore(summary.tryMean)} <span className="muted">(folds from {fmtScore(summary.tryLow)} to {fmtScore(summary.tryHigh)})</span></> },
          { key: "current", label: `${capitalize(against)} (mean over folds)`, value: fmtScore(summary.currentMean) },
          { key: "folds", label: "Folds compared", value: String(summary.folds) },
        ]} />
      ) : null}
      {summary?.byShortfall ? <p className="muted">One of the two runs misses the goal&apos;s rules, so they were compared by how far each falls short of the rules (lower is better).</p> : null}
      {rows.length > 0 && key !== "not_comparable" ? <details><summary>Fold by fold</summary><Folds rows={rows} name={name} against={against} /></details> : null}
      <p className="muted">
        Created {formatWhen(it.created_at)}{it.finished_at ? `, finished ${formatWhen(it.finished_at)}` : ""}.
        {runHref ? <> <Link href={runHref}>Open this try&apos;s run</Link></> : null}
      </p>
    </li>
  );
}

function TriesChart({ run }: { run: StudioLoop }) {
  const { rows, leftOut } = chartRows(run);
  if (rows.length === 0) return <div className="empty" role="note"><p>No try has been compared yet. The chart fills in as tries finish.</p></div>;
  const summary = `Goal score per try on cross-validation, mean over folds: ${rows.map((r) => `${r.name} ${r.tried} against ${r.against} ${r.current}`).join("; ")}.`;
  return (
    <>
      <Chart kind="line" data={rows} xKey="name" summary={summary} series={[{ key: "tried", label: "This try" }, { key: "current", label: "Compared with (the current model, or the best try so far)", tone: "muted" }]} />
      <p className="muted">Each point is the mean over the folds. A try counts as better only when its fold-by-fold gain is bigger than the <Term definition={NOISE_TERM}>noise margin</Term>; a higher point alone is not enough. Each try&apos;s fold range is listed below.</p>
      {leftOut ? <p className="muted">{leftOut} {leftOut === 1 ? "try is" : "tries are"} not drawn: compared by the shortfall from the goal&apos;s rules, which is another unit.</p> : null}
    </>
  );
}

function Decision({ run, etag, onDone }: { run: StudioLoop; etag: string | null; onDone: () => void }) {
  const gate = acceptGate(run);
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [picked, setSeq] = useState<number | null>(null);
  const seq = picked !== null && gate.acceptable.includes(picked) ? picked : gate.best ?? (gate.acceptable.length === 1 ? gate.acceptable[0] : null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | string | null>(null);
  if (run.status !== "waiting_user") return null;
  const send = async (keep: boolean) => {
    if (flight.current || !etag) return;
    const body = acceptBody(keep ? { keep: true, reason } : { keep: false, seq, reason });
    if (typeof body === "string") { setError(body); return; }
    flight.current = true;
    setBusy(true); setError(null);
    try {
      await acceptImproveLoop({ loopId: run.id, body, etag, key: keys.keyFor(keep ? "keep" : "accept", JSON.stringify(body)) });
      keys.done(keep ? "keep" : "accept");
      onDone();
    } catch (caught) {
      setError(loopError(caught));
      if (isStale(caught)) onDone();
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <Card title="Your decision" aside={<Pill tone="warn">waiting for you</Pill>}>
      <p>A try beat the noise on cross-validation. Accept it, or keep the current model. {ONE_LOOK_NOTE}</p>
      <p className="muted">Keeping the current model is a normal result: nothing is scored and the final test set stays unused.{run.waiting_user_expires_at ? ` Without a decision by ${formatWhen(run.waiting_user_expires_at)}, the current model is kept.` : ""}</p>
      {stopDetailText(run.stop_detail) ? <Banner tone="warn">{stopDetailText(run.stop_detail)} You can accept the same try again (at most three final runs in all) or keep the current model.</Banner> : null}
      {gate.note ? <p className="muted" role="note">{gate.note}</p> : null}
      {gate.canAccept || gate.canKeep ? (
        <form className="form" aria-label="Accept a try or keep the current model" onSubmit={(e) => { e.preventDefault(); void send(false); }}>
          {gate.canAccept ? (
            <fieldset className="loop-choices" disabled={busy}>
              <legend>Try to accept</legend>
              {gate.acceptable.map((s) => {
                const it = run.iterations.find((i) => i.seq === s);
                const tag = gate.best === null ? "" : s === gate.best ? " (best so far)" : " (later beaten beyond the noise by another try)";
                return (
                  <label key={s} className="loop-choice">
                    <input type="radio" name="loop-try" checked={seq === s} onChange={() => setSeq(s)} />
                    <span>Try {s}{it ? ` · ${tryChange(it)}` : ""}{tag}</span>
                  </label>
                );
              })}
              {gate.acceptable.length > 1 && gate.best !== null ? <p className="muted">Each later try was compared with the best try so far. The best so far beat the others beyond the noise, so it is the one to accept.</p> : null}
            </fieldset>
          ) : null}
          <label className="field"><span>Your reason (saved with the decision{gate.canAccept ? "; required to accept" : ""})</span>
            <textarea rows={2} maxLength={REASON_MAX} value={reason} disabled={busy} onChange={(e) => setReason(e.target.value)} />
          </label>
          {!etag ? <p className="muted" role="note">The loop&apos;s version could not be read, so it cannot be changed from here. Reload the page.</p> : null}
          {error ? <Banner tone="crit">{typeof error === "string" ? error : <><b>{error.title}.</b> {error.detail}</>}</Banner> : null}
          <div className="toolbar">
            {gate.canAccept ? <button type="submit" className="btn primary" disabled={busy || !etag || seq === null}>{busy ? "Sending…" : "Accept this try (one final run, one final-test number)"}</button> : null}
            {gate.canKeep ? <button type="button" className="btn" disabled={busy || !etag} onClick={() => void send(true)}>Keep the current model</button> : null}
          </div>
        </form>
      ) : null}
    </Card>
  );
}

function FinalTest({ run, projectId }: { run: StudioLoop; projectId: string }) {
  const finalId = run.final_experiment_id ?? null;
  const showsRun = run.status === "finalizing" || (run.outcome === "improved" && run.sealed_set_consulted);
  const final = useFinalRun(showsRun ? finalId : null, run.status === "finalizing");
  if (!showsRun || !finalId) return null;
  const view = finalTestView(run, run.status === "finalizing" ? null : final.data?.metrics?.holdout ?? null);
  const href = projectHref(projectId, "experiments", finalId);
  return (
    <Card title="Final test (scored once, for the accepted try)" aside={<Pill tone="det">one look at the final test set</Pill>}>
      {view.state === "running" ? (
        <>
          <p>{view.text}</p>
          <p role="status">{final.isPending ? "Reading the final run…" : final.isError ? "The final run's state could not be read; the loop page updates on its own." : `Final run: ${statusWords(final.data!.status)}.`}</p>
        </>
      ) : view.state === "no_number" ? <p>{view.text}</p> : final.isPending ? <p role="status">Reading the final run…</p> : final.isError ? (
        <Banner tone="warn">{readState(final.error) === "forbidden" ? "Your role cannot read the final run." : "The final run could not be read. Reload the page to try again."}</Banner>
      ) : view.state === "reported" ? (
        <>
          <p className="loop-final"><span className="muted">Final test, {view.metric}:</span> <b>{view.value}</b></p>
          <p className="muted">This is the one number of the final test set for this loop, measured once after you accepted the try. It was not used to choose anything. Compare it with cross-validation; never pick a model by it.</p>
        </>
      ) : view.state === "missing" ? <p>{view.text}</p> : null}
      {href && view.state !== "no_number" ? <p><Link href={href}>Open the final run</Link>{view.state === "running" ? null : " (put its model in use from there if you want it)"}.</p> : null}
    </Card>
  );
}

function CancelButton({ run, etag, onDone }: { run: StudioLoop; etag: string | null; onDone: () => void }) {
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const confirmRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => { if (asking) confirmRef.current?.focus(); }, [asking]);
  if (!run.actions.cancel) return null;
  const send = async () => {
    if (flight.current || !etag) return;
    flight.current = true;
    setBusy(true); setError(null);
    try {
      await cancelImproveLoop({ loopId: run.id, etag, key: keys.keyFor("cancel", run.id) });
      keys.done("cancel");
      setAsking(false);
      onDone();
    } catch (caught) {
      setError(loopError(caught));
      if (isStale(caught)) onDone();
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  const question = run.status === "finalizing" ? "Stop the final run? If its final test already started, that look still counts."
    : run.status === "waiting_user" ? "Stop the loop without deciding? No try is accepted and the final test set stays unused."
    : "Stop the loop? Its running try is stopped and the best so far is kept.";
  return (
    <div className="loop-cancel">
      {!asking ? <button type="button" className="btn" disabled={!etag} onClick={() => setAsking(true)}>Stop the loop</button> : (
        <div className="toolbar" role="group" aria-label="Confirm stopping the loop" ref={confirmRef} tabIndex={-1}>
          <span>{question}</span>
          <button type="button" className="btn primary" disabled={busy} onClick={() => void send()}>{busy ? "Stopping…" : "Yes, stop it"}</button>
          <button type="button" className="btn" disabled={busy} onClick={() => setAsking(false)}>No</button>
        </div>
      )}
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
    </div>
  );
}

export function ImproveLoopView({ projectId, loopId }: { projectId: string; loopId: string }) {
  const read = useImproveLoop(loopId);
  const invalidate = useLoopInvalidation();
  const backHref = projectHref(projectId, "improve");
  const back = backHref ? <Link className="btn" href={backHref}>All loops</Link> : null;
  const head = (badges?: ReactNode, actions?: ReactNode) => (
    <PageHead title="Improvement loop" badges={badges} actions={<>{actions}{back}</>}
      subtitle="Sealed tries on the same folds, each judged fold by fold against the noise; the final test set is used once, only for a try you accept." />
  );
  if (!isUuid(loopId)) return <>{head()}<Banner tone="warn">This is not a valid loop link.{backHref ? <> <Link href={backHref}>See the loops of this project</Link>.</> : null}</Banner></>;
  if (read.isError) return <>{head()}<Banner tone={readState(read.error) === "error" ? "crit" : "warn"}>{READ_TEXT[readState(read.error)]}</Banner></>;
  if (read.isPending) return <>{head()}<p role="status">Loading the loop…</p></>;
  const { data: run, etag } = read.data;
  if (run.project_id !== projectId) return <>{head()}<Banner tone="warn">This loop belongs to another project.</Banner></>;
  const status = loopStatus(run.status);
  const spend = spendView(run, Date.now());
  const outcome = outcomeSentence(run);
  const stop = stopReasonText(run.stop_reason);
  const detail = run.status === "waiting_user" ? null : stopDetailText(run.stop_detail);
  const done = () => invalidate(run.id, projectId);
  const startHref = projectHref(projectId, "experiments", run.start_experiment_id);
  const tries = run.iterations;
  return (
    <>
      {head(<Pill tone={status.tone}>{status.label}</Pill>, <CancelButton run={run} etag={etag} onDone={done} />)}
      <StepBar label="Loop progress" steps={loopSteps(run)} />
      <PageGuide
        purpose="Follow one improvement loop: what it tried, what each try showed against the noise, and what it spent."
        howTo="Read the tries as they finish. When the loop waits for you, accept the try that beat the noise or keep the current model."
        youGet="Each try's change and fold-by-fold verdict, the tries chart, the spend, why it stopped, and after an accepted try one final-test number."
        attention={<>{WITHIN_NOISE_NOTE} {SEALED_NOTE}</>}
      />

      <Card title="Where it stands" aside={<Pill tone="det">cross-validation only</Pill>}>
        {outcome ? <p><b>{outcome}</b></p> : null}
        {stop ? <p>{stop}</p> : null}
        {detail && detail !== outcome ? <p className="muted">{detail}</p> : null}
        {!outcome && !stop && run.status !== "waiting_user" ? <p role="status">{run.status === "queued" ? "Waiting for a worker to start the loop." : run.status === "finalizing" ? "The accepted try is being re-run once." : "Trying changes one at a time. This page updates on its own."}</p> : null}
        <KeyValue items={[
          { key: "goal", label: "Goal", value: goalWords(run.goal) },
          { key: "tries", label: "Tries", value: spend.tries },
          { key: "elapsed", label: "Time", value: spend.elapsed },
          { key: "training", label: "Training time", value: spend.training },
          { key: "ai", label: "AI cost", value: spend.ai },
          { key: "folds", label: "Folds per comparison", value: String(run.validation_folds) },
          { key: "sealed", label: "Final test set", value: run.sealed_set_consulted ? "Used once, by the accepted try's final run." : `Not used by this loop. ${sealedSetText(run.sealed_set_label)}` },
          { key: "start", label: "Started from", value: <>{startHref ? <Link href={startHref}>the starting run</Link> : "the starting run"}, {formatWhen(run.created_at)}, by {run.started_by === "service_token" ? "a connected tool" : "a person"}</> },
        ]} />
      </Card>

      <Decision run={run} etag={etag} onDone={done} />
      <FinalTest run={run} projectId={projectId} />

      <Card title="Tries chart" aside={<Pill tone="det">goal score, mean over folds</Pill>}>
        <TriesChart run={run} />
      </Card>

      <Card title="Tries" aside={<Pill tone="det">each try sealed</Pill>}>
        <p className="muted">{WITHIN_NOISE_NOTE}</p>
        {tries.length === 0 ? <div className="empty" role="note"><p>No tries yet. The first appears when the loop starts.</p></div> : (
          <ul className="plain-list" aria-label="Tries">
            {tries.map((it) => <TryItem key={it.seq} it={it} run={run} projectId={projectId} />)}
          </ul>
        )}
      </Card>
    </>
  );
}
