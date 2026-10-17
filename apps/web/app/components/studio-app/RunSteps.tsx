"use client";

/**
 * The timeline of one run in plain words (V7-A3, D2b). Steps follow the engine's real stage order (a first look at the
 * whole file, then the final test set is set aside, then the training rows alone are profiled) with the time each
 * recorded; each step opens to its engine stages (the ones the run recorded), each with its own time. Stages come from `GET /v1/model-builds/{id}`; a step the run did not record is left
 * out. Tries come from `attempts` on `GET /v1/experiments/{id}` (D2a): "retried automatically" only when the read says
 * `retried`. The final test set step shows no score: it only says the set is scored once per run.
 */
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { Card } from "@/components/studio/Card";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { durationLabel } from "@/lib/application/studio-pipeline";
import { useRunBuild } from "@/lib/application/studio-pipeline-hooks";
import { STEP_STATE_WORDS, attemptRows, attemptsSummary, runSteps, type AttemptLike, type StepState } from "@/lib/application/studio-runs";

const TONE: Record<StepState, PillTone> = { done: "ok", partly: "warn", running: "ai", failed: "crit", skipped: "gray", stopped: "gray", waiting: "gray" };

export function RunSteps({ experimentId, attempts }: { experimentId: string; attempts?: AttemptLike[] }) {
  const build = useRunBuild(experimentId);
  const steps = runSteps(build.data?.stages ?? []);
  const timed = steps.map((s) => s.ms).filter((ms): ms is number => ms !== null);
  const total = timed.length ? timed.reduce((a, b) => a + b, 0) : null;
  const longest = Math.max(0, ...timed);
  const tries = attemptRows(attempts);
  const triesLine = attemptsSummary(tries, attempts);
  return (
    <Card title="Steps of this run" aside={total === null ? undefined : `${durationLabel(total)} in the recorded steps`}>
      {build.isError ? <QueryNotice error={build.error} what="steps of this run" /> : null}
      {build.isPending ? <p role="status">Loading the steps…</p> : null}
      {build.data && steps.length === 0 ? <div className="empty" role="note">No step has been recorded for this run yet.</div> : null}
      {steps.length ? (
        <div className="tbl">
          <table>
            <caption className="sr-only">Steps of this run and the time each took</caption>
            <thead><tr><th scope="col">Step</th><th scope="col" className="r">Time</th><th scope="col">State</th></tr></thead>
            <tbody>
              {steps.map((step, index) => (
                <tr key={step.id} data-step={step.id}>
                  <td>
                    <span className="muted">{index + 1}.</span> {step.label}
                    {step.ms !== null && longest > 0 ? <span aria-hidden="true" style={{ display: "block", height: 4, marginTop: 4, width: `${Math.max(2, Math.round((step.ms / longest) * 100))}%`, background: "var(--accent)", borderRadius: 2 }} /> : null}
                    {step.parts.length ? (
                      <details className="run-step-parts">
                        <summary>{step.parts.length === 1 ? "1 engine stage" : `${step.parts.length} engine stages`}</summary>
                        <ol aria-label={`Engine stages of ${step.label}, in run order`}>
                          {step.parts.map((part) => <li key={part.key} data-stage={part.key}>{part.label}: <span className="muted">{part.state}, {part.time}</span></li>)}
                        </ol>
                      </details>
                    ) : null}
                  </td>
                  <td className="r">{step.time}</td>
                  <td><Pill tone={TONE[step.state]}>{STEP_STATE_WORDS[step.state]}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {tries.length ? (
        <div data-testid="run-attempts">
          <h3>Tries</h3>
          <p className="muted">{triesLine}</p>
          <ol className="plain-list" aria-label="Tries of this run, oldest first">
            {tries.map((row) => (
              <li key={row.key}><b>{row.label}</b> <Pill tone={row.tone}>{row.sentence}</Pill> <span className="muted">{row.time}</span></li>
            ))}
          </ol>
        </div>
      ) : null}
      <p className="muted small">Every model is trained once for each fold. The final test set is scored once in this run, after the best model is chosen on cross-validation; its scores are not shown here.</p>
    </Card>
  );
}
