"use client";

/**
 * Improve (D4e = P5.5-A, ADR 0007 option A): the page heading, the "Improvement loop" tab (start a loop with a goal and a budget,
 * and the project's loops) and the "Try one change yourself" tab (the guided screen, V7-B4). The goal is read from the project's
 * current state and is not edited here. The server decides who may start a loop and refuses what it cannot judge; this page shows
 * its refusals as fixed sentences. Untrusted text is rendered as text; no final-test value is read on this tab.
 */
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useRef, useState } from "react";
import { ImproveScreen } from "@/app/components/studio-app/ImproveScreen";
import { formatWhen } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { TabPanel, Tabs } from "@/components/studio/Tabs";
import { useProjectRefs } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";
import { useExperimentDetail, useModelVersionRead } from "@/lib/application/studio-inspect-hooks";
import { useOperatingPoints } from "@/lib/application/studio-operating-hooks";
import {
  BUDGET_LIMITS, DEFAULT_BUDGET_INPUT, FRESH_SPLIT_NOTE, ONE_LOOK_NOTE, SEALED_NOTE, WITHIN_NOISE_NOTE,
  goalFromState, improveLoopHref, loopError, loopStatus, outcomeSentence, readState, spendView, startBody, stopReasonText, validateBudget, type BudgetInput,
} from "@/lib/application/studio-improve-loop";
import { startImproveLoop, useImproveLoops, useLoopInvalidation, type StudioLoop } from "@/lib/application/studio-improve-loop-hooks";
import { useSession } from "@/lib/application/session-provider";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

const TABS = [
  { id: "loop", label: "Improvement loop" },
  { id: "guided", label: "Try one change yourself" },
];
const VIEWER_NOTE = "Your role can read this page but not start a loop. Ask someone who can change this workspace.";

function ReadFailure({ error, what }: { error: unknown; what: string }) {
  const state = readState(error);
  if (state === "forbidden") return <Banner tone="warn">Your role cannot see {what} in this workspace.</Banner>;
  if (state === "not_found") return <Banner tone="warn">The {what} could not be found in the active workspace, or you cannot see it.</Banner>;
  return <Banner tone="crit">The {what} could not be loaded. Reload the page to try again.</Banner>;
}

function BudgetField({ id, label, unit, value, error, limits, disabled, onChange, extra }: {
  id: keyof BudgetInput; label: string; unit: string; value: string; error?: string; limits: { default: number; max: number; min: number }; disabled: boolean; onChange: (v: string) => void; extra?: string;
}) {
  const hintId = `loop-budget-${id}-hint`;
  const errorId = `loop-budget-${id}-error`;
  return (
    <label className="field">
      <span>{label}</span>
      <input type="number" inputMode="numeric" min={limits.min} max={limits.max} step={1} value={value} disabled={disabled}
        aria-invalid={error ? true : undefined} aria-describedby={error ? `${hintId} ${errorId}` : hintId} onChange={(e) => onChange(e.target.value)} />
      <small id={hintId} className="muted">Default {limits.default} {unit}, at most {limits.max}.{extra ? ` ${extra}` : ""}</small>
      <small id={errorId} className="loop-field-error" aria-live="polite">{error ?? ""}</small>
    </label>
  );
}

/** Start a loop from the run behind the model in use, with the goal read from the project and a budget within the server's ceilings. */
function LoopStart({ projectId, onGuided }: { projectId: string; onGuided: () => void }) {
  const router = useRouter();
  const { user } = useSession();
  const canRun = hasCapability(user, CAPABILITIES.workspaceExecuteMl);
  const refs = useProjectRefs(projectId);
  const championId = refs.data?.items.find((r) => r.ref_kind === "champion_model")?.target.id ?? null;
  const model = useModelVersionRead(championId);
  const inProject = !!model.data && model.data.project_id === projectId;
  const startId = inProject ? model.data!.lineage.experiment_id : null;
  const detail = useExperimentDetail(startId);
  const operating = useOperatingPoints(startId);
  const invalidate = useLoopInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [budget, setBudget] = useState<BudgetInput>(DEFAULT_BUDGET_INPUT);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const goalHref = projectHref(projectId, "goal");
  const runHref = startId ? projectHref(projectId, "experiments", startId) : null;

  if (refs.isError) return <ReadFailure error={refs.error} what="versions in use" />;
  if (refs.isPending) return <p role="status">Loading the model in use…</p>;
  if (!championId) return <div className="empty" role="note"><p><b>No model is in use yet.</b></p><p>A loop starts from the model in use. Finish a run and put its model in use first.</p></div>;
  if (model.isError) return <ReadFailure error={model.error} what="model in use" />;
  if (!model.data) return <p role="status">Loading the model in use…</p>;
  if (!startId) return <Banner tone="warn">The model in use could not be matched to a run of this project, so no loop can start from here.</Banner>;
  if (detail.isError) return <ReadFailure error={detail.error} what="run in use" />;
  if (!detail.data) return <p role="status">Reading the run in use…</p>;

  const operatingSettled = !operating.isPending;
  const choice = goalFromState({ chosen: operating.data?.chosen ?? null, selectionMetric: detail.data.metrics?.selection_metric ?? null });
  const checked = validateBudget(budget);
  const errors = checked.ok ? {} : checked.errors;
  const set = (field: keyof BudgetInput) => (value: string) => setBudget((b) => ({ ...b, [field]: value }));
  const send = async () => {
    if (flight.current || !checked.ok || !choice.goal || !startId) return;
    flight.current = true;
    setBusy(true); setError(null);
    const body = startBody(startId, choice.goal, checked.budget);
    try {
      const created = await startImproveLoop({ body, key: keys.keyFor("start", JSON.stringify(body)) });
      keys.done("start");
      invalidate(created.id, projectId);
      const href = improveLoopHref(projectId, created.id);
      if (href) router.push(href);
    } catch (caught) {
      setError(loopError(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };

  return (
    <form className="form loop-start" aria-label="Start an improvement loop" onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <div className="field">
        <span>Goal (from the project; not changed here)</span>
        {!operatingSettled ? <p role="status">Reading the goal…</p> : <p><b>{choice.words}</b></p>}
        {operatingSettled && choice.goal ? (
          <p className="muted">
            {"source" in choice && choice.source === "operating_point" ? "From the threshold rule recorded on the run in use." : "The score models are ranked on. To aim at precision or recall under a rule, record a threshold rule first."}
            {" "}{goalHref ? <Link href={goalHref}>Change the goal</Link> : null}{goalHref ? " or " : ""}
            <button type="button" className="loop-link" onClick={onGuided}>set a threshold rule</button>.
          </p>
        ) : null}
        {operating.isError ? <p className="muted" role="note">The threshold choice could not be read, so the goal is the score models are ranked on.</p> : null}
      </div>
      <p className="muted">Starts from {runHref ? <Link href={runHref}>the run behind the model in use</Link> : "the run behind the model in use"}. {FRESH_SPLIT_NOTE}</p>
      <fieldset className="loop-budget" disabled={busy || !canRun}>
        <legend>Budget</legend>
        <BudgetField id="tries" label="Most tries" unit="tries" value={budget.tries} error={errors.tries} limits={BUDGET_LIMITS.tries} disabled={busy} onChange={set("tries")} />
        <BudgetField id="minutes" label="Most time, in minutes" unit="minutes" value={budget.minutes} error={errors.minutes} limits={BUDGET_LIMITS.minutes} disabled={busy} onChange={set("minutes")} extra="If less time is left than the next try needs, the loop stops at once." />
        <BudgetField id="patience" label="Stop after this many tries in a row without a gain (patience)" unit="tries" value={budget.patience} error={errors.patience} limits={BUDGET_LIMITS.patience} disabled={busy} onChange={set("patience")} />
        <div className="field">
          <span>Most AI cost</span>
          <p><Pill tone="gray">AI off — $0</Pill> <span className="muted">The tries come from fixed rules; no AI is used.</span></p>
        </div>
      </fieldset>
      <div>
        <p><b>What happens</b></p>
        <ul className="plain-list tight">
          <li>The loop tries one change at a time: for a goal with a rule, the threshold that meets it first (no retraining); then each model family this run leaves out.</li>
          <li>{SEALED_NOTE} Each try is compared with the current model on the same folds, fold by fold, against the noise.</li>
          <li>It stops when the budget or the patience runs out, or nothing is left to try. The best so far is kept.</li>
          <li>When the loop stops and a try beat the noise, it waits for you. {ONE_LOOK_NOTE}</li>
          <li>{WITHIN_NOISE_NOTE}</li>
        </ul>
      </div>
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
      {!canRun ? <p className="muted" role="note">{VIEWER_NOTE}</p> : (
        <div className="toolbar">
          <button type="submit" className="btn primary" disabled={busy || !checked.ok || !choice.goal || !operatingSettled}>{busy ? "Starting…" : "Start the loop"}</button>
          {!choice.goal ? <span className="muted" role="status">{choice.words}</span> : null}
        </div>
      )}
    </form>
  );
}

function LoopList({ projectId }: { projectId: string }) {
  const loops = useImproveLoops(projectId);
  if (loops.isError) return <ReadFailure error={loops.error} what="improvement loops" />;
  if (loops.isPending) return <p role="status">Loading the loops…</p>;
  const items = loops.data.items;
  if (items.length === 0) return <div className="empty" role="note"><p>No loops yet. Start one above; it appears here with its outcome.</p></div>;
  const now = Date.now();
  const columns: Column<StudioLoop>[] = [
    { key: "when", header: "Started", render: (l) => { const href = improveLoopHref(projectId, l.id); const when = formatWhen(l.created_at); return href ? <Link href={href}>{when}</Link> : when; } },
    { key: "status", header: "State", render: (l) => { const s = loopStatus(l.status); return <Pill tone={s.tone}>{s.label}</Pill>; } },
    { key: "tries", header: "Tries", render: (l) => spendView(l, now).tries },
    { key: "outcome", header: "Outcome", render: (l) => outcomeSentence(l) ?? (l.status === "waiting_user" ? "A try beat the noise: waiting for your decision." : l.status === "finalizing" ? "Final run in progress." : stopReasonText(l.stop_reason) ?? "Still running.") },
  ];
  return (
    <>
      <DataTable caption="Improvement loops of this project, newest first" columns={columns} rows={items} rowKey={(l) => l.id} />
      {items.length >= 20 ? <p className="muted">Only the newest 20 loops are listed.</p> : null}
    </>
  );
}

function LoopTab({ projectId, onGuided }: { projectId: string; onGuided: () => void }) {
  return (
    <>
      <PageGuide
        purpose="Let DCLab try changes for you within a budget, and keep the current model unless a change clearly beats the noise."
        howTo="Check the goal, set the budget and start the loop. Watch the tries. When the loop stops and a try beat the noise, it waits for you: accept it or keep the current model."
        youGet="Each try with its change and its fold-by-fold verdict, what was spent, why the loop stopped, and after an accepted try one final-test number."
        attention={<>{WITHIN_NOISE_NOTE} {ONE_LOOK_NOTE}</>}
      />
      <Card title="Start an improvement loop" aside={<Pill tone="det">calculated by fixed rules, no AI</Pill>}>
        <LoopStart projectId={projectId} onGuided={onGuided} />
      </Card>
      <Card title="Loops of this project">
        <LoopList projectId={projectId} />
      </Card>
    </>
  );
}

function ImproveHomeInner({ projectId }: { projectId: string }) {
  const requested = useSearchParams().get("tab");
  const [tab, setTab] = useState(() => (TABS.some((t) => t.id === requested) ? (requested as string) : "loop"));
  return (
    <>
      <PageHead title="Improve" subtitle="Try changes to the model in use, see whether a gain is bigger than the noise, and keep the current model when it is not." />
      <Tabs items={TABS} value={tab} onChange={setTab} idPrefix="improve" label="Ways to improve" />
      <TabPanel idPrefix="improve" id="loop" active={tab === "loop"}>{tab === "loop" ? <LoopTab projectId={projectId} onGuided={() => setTab("guided")} /> : null}</TabPanel>
      <TabPanel idPrefix="improve" id="guided" active={tab === "guided"}>{tab === "guided" ? <ImproveScreen projectId={projectId} embedded /> : null}</TabPanel>
    </>
  );
}

export function ImproveHome({ projectId }: { projectId: string }) {
  return <Suspense fallback={<p role="status">Loading…</p>}><ImproveHomeInner projectId={projectId} /></Suspense>;
}

