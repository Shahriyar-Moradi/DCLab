"use client";

/**
 * Findings panel: the trust checks of one run in the order they run (the server's check registry, D2b). Every value is a field of
 * `GET /v1/experiments/{id}/findings`; the message is shown exactly as the API returns it, evidence is
 * plain text (column names are user data) and no final test set value exists in the payload. "What to do" links to
 * the Branch form pre-filled only when the recommendation maps to a typed change.
 */
import { GLOSSARY } from "@/components/studio/glossary";
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
  RECOMMENDATION_TEXT, branchPrefillHref, checkLabel, checkList, evidenceRows, passSummary, findingStatusLabel, findingTone, findingsState,
} from "@/lib/application/studio-findings";

export const FINDING_TERMS = {
  leakage: GLOSSARY.leakage.definition,
  overfit: "The gap between the score on the rows the model trained on and on unseen cross-validation folds. A large gap means it may be memorizing.",
  duplicates: "Rows that repeat another row on every model column. Repeats inside training weigh twice; repeats across the split make the test score partly a memory test.",
  imbalance: "One class is much rarer than another, so accuracy can look good while the rare class is mostly missed.",
  baseline: GLOSSARY.baseline.definition,
  calibration: "Whether a predicted 30% really happens about 30% of the time, measured on cross-validation predictions.",
  drift: GLOSSARY.drift.definition + " Here only column values are compared, never the test outcomes or scores.",
};

export function FindingsPanel({ projectId, experimentId }: { projectId: string; experimentId: string }) {
  const query = useExperimentFindings(experimentId);
  const data = query.data;
  const state = findingsState(data);
  // Shown in the order the read sends them: the registry's run order, not re-sorted here.
  const checks = data?.checks ?? [];
  const list = checkList(data);
  const summary = passSummary(data);
  return (
    <div aria-label="Trust checks" data-testid="findings-panel">
      <p className="muted">
        Trust checks run on every finished run and ask whether the score can be believed: does a column{" "}
        <Term definition={FINDING_TERMS.leakage}>give away the answer</Term>, is the model <Term definition={FINDING_TERMS.overfit}>memorizing the training rows</Term>,
        are there <Term definition={FINDING_TERMS.duplicates}>repeated rows</Term>, is the outcome <Term definition={FINDING_TERMS.imbalance}>rare</Term>, and is the score too good against the{" "}
        <Term definition={FINDING_TERMS.baseline}>baseline</Term>. Newer runs also check whether scores are steady between folds, whether predicted chances match reality (<Term definition={FINDING_TERMS.calibration}>calibration</Term>),
        a weak group, repeated columns, <Term definition={FINDING_TERMS.drift}>differences between training and test rows</Term>, the time order of the split, training rows showing up in the test set, future information in a column and one new column that moved the score a lot.
        They never read the final test set&apos;s outcomes, predictions or scores, and work with AI switched off.
      </p>
      {query.isError ? <QueryNotice error={query.error} what="findings" /> : null}
      {query.isPending ? <p role="status">Loading findings…</p> : null}
      {state === "not_computed" ? (
        <div className="empty" role="note">
          <p><b>No trust checks recorded yet.</b></p>
          <p>The trust checks are stored when a run finishes. This run has not finished, or it finished before the checks existed. Not checked is not the same as passed.</p>
        </div>
      ) : null}
      {data && state !== "not_computed" ? (
        <p role="status" className="toolbar">
          {summary ? <Pill tone={summary.tone}>{summary.pill}</Pill> : null}
          <span className="muted">
            {data.summary?.passed ?? 0} passed · {data.summary?.warnings ?? 0} to review · {data.summary?.failures ?? 0} failed · {data.summary?.not_evaluated ?? 0} not checked
            {data.version ? ` · ${plainText(data.version, 40)}` : ""}
          </span>
        </p>
      ) : null}
      {summary?.sentence ? <Banner tone="info">{summary.sentence}</Banner> : null}
      {list.length ? (
        <div className="tbl">
          <table data-testid="check-list">
            <caption className="sr-only">The {list.length} trust checks, in the order they run</caption>
            <thead><tr><th scope="col" className="r">Order</th><th scope="col">Check</th><th scope="col">Result</th></tr></thead>
            <tbody>
              {list.map((row) => (
                <tr key={row.check} data-check-row={row.check}>
                  <td className="r">{row.position}</td>
                  <th scope="row">{plainText(row.label, 80)}</th>
                  <td><Pill tone={row.tone}>{plainText(row.result, 60)}</Pill></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {checks.map((finding) => {
        const href = branchPrefillHref(projectId, experimentId, finding);
        const safe = href ? safeInternalHref(href) : null;
        const rows = evidenceRows(finding.evidence);
        const recommendation = finding.recommendation_kind ? (Object.hasOwn(RECOMMENDATION_TEXT, finding.recommendation_kind) ? RECOMMENDATION_TEXT[finding.recommendation_kind] : null) ?? plainText(finding.recommendation_kind.replaceAll("_", " "), 120) : null;
        return (
          <article key={finding.check} className="card flat" aria-label={`Trust check ${checkLabel(finding.check)}`} data-check={finding.check}>
            <p className="toolbar">
              <Pill tone={findingTone(finding.status)}>{findingStatusLabel(finding.status, finding.severity)}</Pill>
              <b>{checkLabel(finding.check)}</b>
              <span className="muted">how serious: {plainText(finding.severity, 20)}</span>
            </p>
            <p>{plainText(finding.message, 2000)}</p>
            {rows.length ? (
              <details open={finding.status !== "pass"}>
                <summary>The numbers behind this check ({rows.length})</summary>
                <KeyValue items={rows.map((row) => ({ key: row.key, label: plainText(row.label, 80), value: <span className="mono">{plainText(row.value, 300)}</span> }))} />
              </details>
            ) : null}
            {recommendation ? (
              <p>
                <b>What to do. </b>{recommendation}{" "}
                {safe ? <Link className="btn" href={safe} data-testid="finding-branch-link">Try this change</Link> : <span className="muted">(no change can be started from here; this one is a data decision)</span>}
              </p>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}
