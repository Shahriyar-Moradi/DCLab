"use client";

/**
 * The steps of one run in plain words with the time each recorded (V7-A3). Stages and their durations come from
 * `GET /v1/model-builds/{id}` (the same record the Run evidence page shows); a step the run did not record is left out.
 * The final test set step shows no score: it only says the set is scored once per run.
 */
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { Card } from "@/components/studio/Card";
import { Pill, type PillTone } from "@/components/studio/Pill";
import { durationLabel } from "@/lib/application/studio-pipeline";
import { useRunBuild } from "@/lib/application/studio-pipeline-hooks";
import { STEP_STATE_WORDS, runSteps, type StepState } from "@/lib/application/studio-runs";

const TONE: Record<StepState, PillTone> = { done: "ok", partly: "warn", running: "ai", failed: "crit", skipped: "gray", stopped: "gray", waiting: "gray" };

export function RunSteps({ experimentId }: { experimentId: string }) {
  const build = useRunBuild(experimentId);
  const steps = runSteps(build.data?.stages ?? []);
  const timed = steps.map((s) => s.ms).filter((ms): ms is number => ms !== null);
  const total = timed.length ? timed.reduce((a, b) => a + b, 0) : null;
  const longest = Math.max(0, ...timed);
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
              {steps.map((step) => (
                <tr key={step.id} data-step={step.id}>
                  <td>
                    {step.label}
                    {step.ms !== null && longest > 0 ? <span aria-hidden="true" style={{ display: "block", height: 4, marginTop: 4, width: `${Math.max(2, Math.round((step.ms / longest) * 100))}%`, background: "var(--accent)", borderRadius: 2 }} /> : null}
                  </td>
                  <td className="r">{step.time}</td>
                  <td><Pill tone={TONE[step.state]}>{STEP_STATE_WORDS[step.state]}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      <p className="muted small">Every model is trained once for each fold. The final test set is scored once in this run, after the best model is chosen on cross-validation; its scores are not shown here.</p>
    </Card>
  );
}
