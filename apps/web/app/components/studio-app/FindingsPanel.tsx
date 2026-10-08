"use client";

/**
 * Findings panel (P4.10-UI): the trust checks of one run, worst first. Every value is a field of
 * `GET /v1/experiments/{id}/findings`; the message is shown exactly as the API returns it, evidence is
 * plain text (column names are user data) and no holdout value exists in the payload. "What to do" links to
 * the Branch form pre-filled only when the recommendation maps to a typed change.
 */
import Link from "next/link";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { KeyValue } from "@/components/studio/KeyValue";
import { Pill } from "@/components/studio/Pill";
import { safeInternalHref } from "@/components/studio/safe-href";
import { Term } from "@/components/studio/Term";
import { plainText } from "@/lib/application/command-search";
import { useExperimentFindings } from "@/lib/application/studio-data-hooks";
import {
  RECOMMENDATION_TEXT, attentionCount, branchPrefillHref, checkLabel, evidenceRows, findingStatusLabel, findingTone, findingsState, sortFindings,
} from "@/lib/application/studio-findings";

export const FINDING_TERMS = {
  leakage: "Information in a column that would not exist at prediction time (for example the answer itself). It makes scores look better than they will be.",
  overfit: "The gap between the score on the rows the model trained on and on unseen cross-validation folds. A large gap means it may be memorizing.",
  duplicates: "Rows that repeat another row on every model column. Repeats inside training weigh twice; repeats across the split make the test score partly a memory test.",
  imbalance: "One class is much rarer than another, so accuracy can look good while the rare class is mostly missed.",
  baseline: "A dummy model (for example always the majority class). A real model has to beat it to be worth anything.",
  calibration: "Whether a predicted 30% really happens about 30% of the time, measured on cross-validation predictions.",
  drift: "How differently a column is distributed in the test rows than in the training rows. Only column values are compared, never the test outcomes or scores.",
};

export function FindingsPanel({ projectId, experimentId }: { projectId: string; experimentId: string }) {
  const query = useExperimentFindings(experimentId);
  const data = query.data;
  const state = findingsState(data);
  const checks = sortFindings(data?.checks ?? []);
  const attention = attentionCount(data);
  return (
    <div aria-label="Findings" data-testid="findings-panel">
      <p className="muted">
        Trust checks run on every finished run:{" "}
        <Term definition={FINDING_TERMS.leakage}>target leakage</Term>, the <Term definition={FINDING_TERMS.overfit}>overfit gap</Term>,{" "}
        <Term definition={FINDING_TERMS.duplicates}>duplicate rows</Term>, <Term definition={FINDING_TERMS.imbalance}>class imbalance</Term>, a score that looks too good against the{" "}
        <Term definition={FINDING_TERMS.baseline}>baseline</Term>, and (for newer runs) fold stability, <Term definition={FINDING_TERMS.calibration}>calibration</Term>, subgroup gaps,
        repeated columns, <Term definition={FINDING_TERMS.drift}>training-to-test drift</Term>, the time order of the split, contamination, time travel and a single new feature&apos;s jump.
        They never read the final holdout&apos;s outcomes, predictions or scores, and work with AI switched off.
      </p>
      {query.isError ? <QueryNotice error={query.error} what="findings" /> : null}
      {query.isPending ? <p role="status">Loading findings…</p> : null}
      {state === "not_computed" ? (
        <div className="empty" role="note">
          <p><b>No findings recorded yet.</b></p>
          <p>The trust checks are stored when a run finishes. This run has not finished, or it finished before the checks existed. This is not the same as all checks passing.</p>
        </div>
      ) : null}
      {data && state !== "not_computed" ? (
        <p role="status" className="toolbar">
          {attention ? <Pill tone="warn">{attention} need{attention === 1 ? "s" : ""} attention</Pill> : <Pill tone="ok">All checks passed</Pill>}
          <span className="muted">
            {data.summary?.passed ?? 0} passed · {data.summary?.warnings ?? 0} warning{data.summary?.warnings === 1 ? "" : "s"} · {data.summary?.failures ?? 0} failed · {data.summary?.not_evaluated ?? 0} not evaluated
            {data.version ? ` · ${plainText(data.version, 40)}` : ""}
          </span>
        </p>
      ) : null}
      {state === "all_passed" ? <Banner tone="info">No findings: all {checks.length} checks passed.</Banner> : null}
      {checks.map((finding) => {
        const href = branchPrefillHref(projectId, experimentId, finding);
        const safe = href ? safeInternalHref(href) : null;
        const rows = evidenceRows(finding.evidence);
        const recommendation = finding.recommendation_kind ? RECOMMENDATION_TEXT[finding.recommendation_kind] ?? plainText(finding.recommendation_kind.replaceAll("_", " "), 120) : null;
        return (
          <article key={finding.check} className="card flat" aria-label={`Finding ${checkLabel(finding.check)}`} data-check={finding.check}>
            <p className="toolbar">
              <Pill tone={findingTone(finding.status)}>{findingStatusLabel(finding.status, finding.severity)}</Pill>
              <b>{checkLabel(finding.check)}</b>
              <span className="muted">severity {plainText(finding.severity, 20)}</span>
            </p>
            <p>{plainText(finding.message, 2000)}</p>
            {rows.length ? (
              <details open={finding.status !== "pass"}>
                <summary>The numbers behind this ({rows.length})</summary>
                <KeyValue items={rows.map((row) => ({ key: row.key, label: plainText(row.label, 80), value: <span className="mono">{plainText(row.value, 300)}</span> }))} />
              </details>
            ) : null}
            {recommendation ? (
              <p>
                <b>What to do. </b>{recommendation}{" "}
                {safe ? <Link className="btn" href={safe} data-testid="finding-branch-link">Branch with this change</Link> : <span className="muted">(no automatic change; this is a data decision)</span>}
              </p>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}
