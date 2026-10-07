"use client";

/**
 * Model card (P4.11-UI). Rendered natively from the API's JSON; the API's Markdown is only offered as a plain-text
 * download (client-side Blob), never rendered. Names, columns, labels and messages are untrusted: plain text only.
 */
import Link from "next/link";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { KeyValue, type KeyValueItem } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { Pill } from "@/components/studio/Pill";
import { Stat } from "@/components/studio/Stat";
import { Term } from "@/components/studio/Term";
import { safeInternalHref } from "@/components/studio/safe-href";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { plainText, projectHref } from "@/lib/application/command-search";
import {
  FINAL_EVAL_HEADING, FINAL_EVAL_SUBHEADING, baselineView, cardFilename, cardNumber, cardTitle, dataRows, driversView, finalView, findingsHref,
  llmLine, markdownDownload, thresholdSourceText, metricName, risksView, splitRows,
} from "@/lib/application/studio-card";
import { useFullModelCard } from "@/lib/application/studio-card-hooks";
import { checkLabel, findingStatusLabel, findingTone } from "@/lib/application/studio-findings";
import { ApiError } from "@/lib/infrastructure/api-client";

const TERMS = {
  importance: "Permutation importance: how much the cross-validation score drops when one column's values are shuffled, measured on validation folds only. Larger means the model leans on that column more.",
  baseline: "A dummy model that ignores the columns (for example always predicts the most common answer or the average). A real model must beat it by a clear margin.",
  holdout: "Rows set aside by the split plan before any modelling. They are scored once, for the locked winner only, and never used to choose the model, its settings or its threshold.",
  operating: "The decision threshold: the probability above which a row is predicted positive. When it was tuned, that was done on cross-validation; it is locked before the final evaluation.",
  cv: "Cross-validation: the training rows are cut into folds; each fold is held out once while the model trains on the others. Models are compared on these scores.",
};

function saveText(text: string, type: string, filename: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function ModelCard({ projectId, modelVersionId }: { projectId: string; modelVersionId: string }) {
  const read = useFullModelCard(modelVersionId);
  if (read.isError) {
    if (read.error instanceof ApiError && read.error.status === 409) {
      return <Banner tone="warn">The card is not available yet. It is built once the run&apos;s evidence is locked.</Banner>;
    }
    return <QueryNotice error={read.error} what="model card" />;
  }
  if (!read.data) return <p role="status">Loading the model card…</p>;
  const card = read.data;
  if (card.project_id && card.project_id !== projectId) return <Banner tone="warn">This model version belongs to another project.</Banner>;

  const markdown = markdownDownload(card);
  const filename = cardFilename(card.model_version_id);
  const drivers = driversView(card.drivers);
  const baseline = baselineView(card.baseline);
  const final = finalView(card.final_evaluation);
  const risks = risksView(card.risks);
  const findings = findingsHref(projectId, card.experiment_id);
  const experimentHref = projectHref(projectId, "experiments", card.experiment_id);
  const t = card.target;
  const o = card.objective;
  const positive = t.task_type === "binary" ? t.positive_label ?? t.positive_label_note : null;

  const predicts: KeyValueItem[] = [
    { key: "target", label: "Target column", value: t.column ? <span className="mono">{plainText(t.column, 80)}</span> : "—" },
    { key: "task", label: "Task", value: plainText(t.task_type, 40) || "—" },
  ];
  if (positive) predicts.push({ key: "pos", label: "Positive class", value: plainText(positive, 120) });
  if (t.class_labels?.length) predicts.push({ key: "classes", label: "Classes", value: t.class_labels.map((c) => plainText(c, 60)).join(", ") });
  if (t.prediction_unit) predicts.push({ key: "unit", label: "Prediction unit", value: plainText(t.prediction_unit, 80) });
  predicts.push({ key: "metric", label: "Primary metric", value: metricName(o.primary_metric) });
  if (o.primary_metric_reason) predicts.push({ key: "why", label: "Why this metric", value: plainText(o.primary_metric_reason, 400) });
  if (o.business_objective) predicts.push({ key: "obj", label: "Business objective", value: plainText(o.business_objective, 400) });
  if (o.decision_threshold != null) predicts.push({ key: "thr", label: <Term definition={TERMS.operating}>Decision threshold</Term>, value: <>{cardNumber(o.decision_threshold)} <span className="muted">{thresholdSourceText(o.decision_threshold_source)}</span></> });

  const facts = [...dataRows(card.data), ...splitRows(card.split)];

  return (
    <div className="model-card-print">
      <div className="mc-noprint">
        <PageGuide
          purpose="One page that explains this model version: what it predicts, how good it is in plain words, what drives it, what could be wrong and what it was built from."
          howTo="Read top to bottom. Print or save as PDF to share it, or download the Markdown to keep it with the project."
          youGet="Drivers, the metric in business words, the baseline comparison, one labelled final evaluation, known risks, data and split counts and whether an LLM was used."
          attention={<>The <Term definition={TERMS.holdout}>final evaluation</Term> is a single look: it never chose the model. The choice was made on <Term definition={TERMS.cv}>cross-validation</Term>.</>}
        />
      </div>
      <header className="mc-head">
        <div>
          <h2 className="mc-title">{cardTitle(card)}</h2>
          <p className="muted">
            Version <span className="mono">{plainText(card.version, 40)}</span> · digest <span className="mono">{plainText(card.content_digest, 80)}</span> · created {formatWhen(card.created_at)}
            {experimentHref ? <> · run <Link href={experimentHref}><span className="mono">{card.experiment_id.slice(0, 8)}</span></Link></> : null}
          </p>
        </div>
        <div className="toolbar mc-noprint">
          <button type="button" className="btn" onClick={() => window.print()}>Print / save as PDF</button>
          <button type="button" className="btn" disabled={!markdown || !filename} onClick={() => markdown && filename && saveText(markdown.text, markdown.type, filename)}>Download Markdown</button>
        </div>
      </header>

      <Card title="What it predicts"><KeyValue items={predicts} /></Card>

      <Card title="How good it is, in plain words" aside={<Pill tone="det">cross-validation</Pill>}>
        <p className="mc-words">{plainText(card.metric_in_words.text, 2000)}</p>
        {card.metric_in_words.caveat ? <p className="muted">{plainText(card.metric_in_words.caveat, 600)}</p> : null}
        <div className="grid cols-4">
          <Stat value={cardNumber(card.cv.mean)} label={`${metricName(card.cv.metric)} (mean)`} hint={card.cv.std != null ? `± ${cardNumber(card.cv.std)} across ${card.cv.folds ?? "n/a"} folds` : undefined} />
          {Object.entries(card.cv.at_locked_threshold ?? {}).sort(([a], [b]) => a.localeCompare(b)).map(([k, v]) => <Stat key={k} value={cardNumber(v)} label={metricName(k)} hint="at the locked threshold, fold mean" />)}
        </div>
        {(o.constraints ?? []).length ? (
          <ul className="plain-list tight" aria-label="Declared constraints">
            {(o.constraints ?? []).map((c) => (
              <li key={`${c.metric}-${c.op}-${c.value}`}>{metricName(c.metric)} {plainText(c.op, 4)} {cardNumber(c.value)}: {cardNumber(c.cv_value)} on cross-validation {c.cv_satisfied === true ? "(met)" : c.cv_satisfied === false ? "(not met)" : "(not evaluated)"}</li>
            ))}
          </ul>
        ) : null}
        {card.cv.threshold_note ? <p className="muted">{plainText(card.cv.threshold_note, 500)}</p> : null}
      </Card>


      <Card title={<>Compared with the <Term definition={TERMS.baseline}>dummy baseline</Term></>} aside={<Pill tone={baseline.tone}>{baseline.badge}</Pill>}>
        <p>{baseline.text}</p>
        {baseline.rows.length ? <KeyValue items={baseline.rows} /> : null}
      </Card>

      <Card title={<>Top drivers (<Term definition={TERMS.importance}>importance</Term>)</>} aside={<Pill tone="det">stored from the run</Pill>}>
        <p>{drivers.text}</p>
        {drivers.state === "bars" ? (
          <>
            <ol className="mc-bars" aria-label="Top drivers by importance">
              {drivers.bars.map((b) => (
                <li key={`${b.rank}-${b.column}`}>
                  <span className="mc-rank">{b.rank}</span>
                  <span className="mc-col mono">{b.column}</span>
                  <span className="meter" aria-hidden="true"><i style={{ width: `${b.width}%` }} /></span>
                  <span className="mc-val">{b.importance}{b.spread ? <span className="muted"> {b.spread}</span> : null}</span>
                  <span className="mc-flag">{b.clear === true ? <Pill tone="ok">clear driver</Pill> : b.clear === false ? <Pill tone="gray">not clearly above noise</Pill> : null}</span>
                </li>
              ))}
            </ol>
            <p className="muted">{drivers.clearCount} of the {drivers.maxShown} columns shown are clear drivers{drivers.clearTotal != null && drivers.clearTotal !== drivers.clearCount ? ` (${drivers.clearTotal} in total)` : ""}. Importance is measured on cross-validation validation folds, never on the final-evaluation rows.</p>
          </>
        ) : (
          <div className="empty" role="note"><p>{drivers.reason}</p></div>
        )}
      </Card>

      <Card title="Known risks" aside={<Pill tone={risks.state === "attention" ? "warn" : risks.state === "clean" ? "ok" : "gray"}>{risks.state === "attention" ? `${risks.attention} need attention` : risks.state === "clean" ? "all checks passed" : "not investigated"}</Pill>}>
        <p>{risks.text}</p>
        {risks.items.length ? (
          <ul className="plain-list" aria-label="Findings summary">
            {risks.items.map((r) => (
              <li key={r.check}><Pill tone={findingTone(r.status)}>{findingStatusLabel(r.status, r.severity)}</Pill> <b>{checkLabel(r.check)}</b>: {r.message}</li>
            ))}
          </ul>
        ) : null}
        {risks.state === "none_run" ? <div className="empty" role="note"><p>The trust checks have not run for this experiment.</p></div> : null}
        {findings && safeInternalHref(findings) ? <p className="mc-noprint"><Link href={findings}>Open the experiment&apos;s Findings card</Link></p> : null}
      </Card>

      <Card title="Data and split" aside={<Pill tone="gray">counts only</Pill>}>
        {facts.length ? <KeyValue items={facts} /> : <div className="empty" role="note"><p>The API reported no data or split summary for this version.</p></div>}
      </Card>

      <Card title="AI involvement">
        <p><b>{llmLine(card.llm)}</b></p>
        <p className="muted">The model itself is trained by deterministic code. An LLM, when used, only advises and every call is recorded.</p>
      </Card>

      <section className="card mc-final" aria-labelledby="mc-final-h">
        <h2 id="mc-final-h">{FINAL_EVAL_HEADING}</h2>
        <p className="muted">{FINAL_EVAL_SUBHEADING} Do not compare final evaluations across versions to pick a champion; compare on cross-validation. See <Term definition={TERMS.holdout}>what the holdout is</Term>.</p>
        {final.state === "reported" ? (
          <>
            <div className="grid cols-4">
              <Stat value={final.value} label={`${final.metric} on the final evaluation`} hint="single scoring of the locked winner" />
              {final.threshold ? <Stat value={final.threshold} label="Decision threshold applied" hint="locked before this look" /> : null}
            </div>
            {final.others.length ? <KeyValue items={final.others.map((m) => ({ key: m.key, label: m.key, value: m.value }))} /> : null}
            <p className="muted">{plainText(card.final_evaluation.label, 300) || "Single final evaluation of the locked winner on held-out rows; never used for selection."}{final.note ? ` ${final.note}` : ""}</p>
          </>
        ) : (
          <div className="empty" role="note"><p>{final.text}</p></div>
        )}
      </section>
    </div>
  );
}
