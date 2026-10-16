"use client";

/**
 * Model → "Final test" (UI-D): the labelled single final evaluation from the model card, beside the cross-validation numbers,
 * with its row count and, for a yes/no model, the 2x2 table of right and wrong answers at the locked threshold. The API sends
 * the table to people only; when it is absent nothing is drawn. No dates of the final test rows are shown.
 */
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { PageGuide } from "@/components/studio/PageGuide";
import { Pill } from "@/components/studio/Pill";
import { Stat } from "@/components/studio/Stat";
import { Term } from "@/components/studio/Term";
import { GLOSSARY } from "@/components/studio/glossary";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { plainText } from "@/lib/application/command-search";
import { FINAL_EVAL_SUBHEADING, cardNumber } from "@/lib/application/studio-card";
import { useFullModelCard } from "@/lib/application/studio-card-hooks";
import { finalTestView } from "@/lib/application/studio-model-detail";
import { ApiError } from "@/lib/infrastructure/api-client";

export function ModelFinalTest({ projectId, modelVersionId }: { projectId: string; modelVersionId: string }) {
  const read = useFullModelCard(modelVersionId);
  if (read.isError) {
    if (read.error instanceof ApiError && read.error.status === 409) return <Banner tone="warn">The final test is not available yet. It is shown once the run&apos;s results are saved and locked.</Banner>;
    return <QueryNotice error={read.error} what="final test" />;
  }
  if (!read.data) return <p role="status">Loading the final test…</p>;
  const card = read.data;
  if (card.project_id && card.project_id !== projectId) return <Banner tone="warn">This model belongs to another project.</Banner>;
  const view = finalTestView(card);
  const positive = card.target.task_type === "binary" ? plainText(card.target.positive_label ?? "", 40) : "";
  const yes = `Really positive${positive ? ` (${positive})` : ""}`;
  const threshold = card.final_evaluation.decision_threshold;
  return (
    <>
      <PageGuide
        purpose="See how this model did on rows it never saw while it was built: the final test, scored once."
        howTo="Compare the final test with cross-validation: similar numbers mean the model holds up on new rows. Read the table of right and wrong answers for a yes/no model."
        youGet="The final test scores beside cross-validation, how many rows were tested, and how many were caught, missed or flagged by mistake."
        attention={<>This model&apos;s run scored the <Term definition={GLOSSARY.finalTest.definition}>final test</Term> once, for this model only; each Improve try scores it again for its own model, so never pick a model by its final-test number.</>}
      />
      <Card title="Final test (used once per run)" aside={<Pill tone="det">scored once by this run</Pill>}>
        <p className="muted">{FINAL_EVAL_SUBHEADING}</p>
        {view.state !== "reported" ? (
          <div className="empty" role="note"><p>{view.state === "withheld" ? "Withheld: this view is for connected tools and the assistant, which never see final test values." : "No final test was reported for this model."}</p></div>
        ) : (
          <>
            <div className="grid cols-4 v7-model-tiles">
              {view.compare.map((m) => <Stat key={m.key} value={m.final} label={`${m.label}, final test`} hint={m.cv === "—" ? "no cross-validation number to compare" : `cross-validation was ${m.cv}`} />)}
              {view.rows ? <Stat value={view.rows} label="rows in the final test" hint={view.setAside ? `set aside by: ${view.setAside}` : "set aside before any modelling"} /> : null}
            </div>
            {view.compare.length ? (
              <div className="tbl card">
                <table>
                  <caption className="sr-only">Cross-validation beside the final test</caption>
                  <thead><tr><th scope="col">Score</th><th scope="col" className="r">Cross-validation</th><th scope="col" className="r">Final test</th></tr></thead>
                  <tbody>{view.compare.map((m) => <tr key={m.key}><th scope="row">{m.label}</th><td className="r">{m.cv}</td><td className="r">{m.final}</td></tr>)}</tbody>
                </table>
              </div>
            ) : null}
            {view.confusion ? (
              <section className="v7-model-confusion" aria-labelledby="v7-model-confusion-h">
                <h3 id="v7-model-confusion-h">Right and wrong answers{threshold != null ? ` at the threshold ${cardNumber(threshold)}` : " (threshold not recorded)"}</h3>
                <div className="tbl card">
                  <table>
                    <caption className="sr-only">Final test rows by what the model said and what really happened</caption>
                    <thead><tr><td /><th scope="col">Model flags</th><th scope="col">Model does not flag</th></tr></thead>
                    <tbody>
                      <tr><th scope="row">{yes}</th><td><b>{view.confusion.tp}</b> caught</td><td><b>{view.confusion.fn}</b> missed</td></tr>
                      <tr><th scope="row">Really negative</th><td><b>{view.confusion.fp}</b> flagged by mistake</td><td><b>{view.confusion.tn}</b> correctly left alone</td></tr>
                    </tbody>
                  </table>
                </div>
                <ul className="plain-list tight v7-model-notes">{view.confusion.lines.map((l) => <li key={l}>{l}</li>)}</ul>
              </section>
            ) : null}
          </>
        )}
      </Card>
    </>
  );
}
