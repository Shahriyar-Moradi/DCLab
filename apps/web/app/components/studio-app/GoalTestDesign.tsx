"use client";

/**
 * Goal & test design (V7-A2, V7 UI-C): what is predicted, how models are ranked, the business rule, and how rows are
 * tested (training folds and a final test set used once). The goal comes from the problem spec read, the test design from
 * the split plan read (row counts only: no final test set dates, rates or statistics), the rule check from the newest run.
 * A value that is not returned is left out. Names, columns, objectives and reasons are untrusted text and render as plain text.
 */
import Link from "next/link";
import { useMemo } from "react";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { useBackingRun } from "@/app/components/studio-app/Inspectors";
import { WhoDecided } from "@/app/components/studio-app/DataGoalD3";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { GlossaryTerm } from "@/components/studio/GlossaryTerm";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { safeInternalHref } from "@/components/studio/safe-href";
import { plainText, projectHref } from "@/lib/application/command-search";
import {
  designChips, goalCards, goalFacts, planDesign, problemSpecOf, timeline, timelineSummary, whyFromRows,
  type GoalCards, type PlanDesign,
} from "@/lib/application/studio-goal";
import { inspectorPath } from "@/lib/application/studio-inspect";
import { useDatasetProfile } from "@/lib/application/studio-data-hooks";
import { useProblemSpec, useProblemSpecs, useSplitPlan } from "@/lib/application/studio-goal-hooks";

const clean = (v: string, max = 120) => plainText(v, max);

function Guide() {
  return (
    <PageGuide
      purpose={<>See what is predicted, what &ldquo;good&rdquo; means and how the data is split so the score is fair: <GlossaryTerm term="cv" /> on the training rows and a <GlossaryTerm term="finalTest" />.</>}
      howTo="Check the target, the ranking metric and the business rule, then the test design. Runs on the same test design are comparable."
      youGet="The goal in three cards, the test design drawn to scale by row count, why it was chosen, and who suggested and confirmed each part."
      attention="The test design is fixed before any model is fitted. Final test set row values and dates are never shown here."
    />
  );
}

/** The three goal cards with big sentences. */
function GoalCardsView({ cards, ruleCheck }: { cards: GoalCards; ruleCheck?: string | null }) {
  const metric = cards.metric;
  return (
    <section className="grid v7-goal-cards" aria-label="The goal">
      <Card title="What we predict">
        <p className="v7-goal-big">{cards.target ? <code>{plainText(cards.target, 80)}</code> : "Not set yet"}</p>
        {cards.question ? <p>&ldquo;{cards.question}&rdquo;</p> : null}
        {cards.task || cards.unitLine ? <p className="muted">{[cards.task, cards.unitLine].filter(Boolean).join(". ")}</p> : null}
      </Card>
      <Card title="How we rank models">
        <p className="v7-goal-big">{metric ? (metric.glossary ? <GlossaryTerm term={metric.glossary}>{metric.label}</GlossaryTerm> : metric.label) : "Not set yet"}</p>
        {cards.metricWhy ? <p className="muted">{cards.metricWhy}</p> : null}
      </Card>
      <Card title="Business rule">
        {cards.rules.map((rule) => (
          <div key={rule.key} className="v7-goal-rule">
            <p className="v7-goal-big">{rule.big}</p>
            <p className="muted">{rule.meaning}</p>
          </div>
        ))}
        {!cards.rules.length ? <><p className="v7-goal-big">{cards.rulesKnown ? "None set" : "Not recorded"}</p><p className="muted">{cards.rulesKnown ? "No business rule was set for this goal." : "The goal could not be read."}</p></> : null}
        {ruleCheck ? <p className="muted">{ruleCheck}</p> : null}
      </Card>
    </section>
  );
}

/** The test design on a line, drawn to scale by row count (no dates): one row per fold, then the final test set. */
function TestTimeline({ design }: { design: PlanDesign }) {
  const t = timeline(design);
  if (!t) return null;
  const X0 = 104;
  const W = 640;
  const ROW = 22;
  const STEP = 32;
  const TOP = 34;
  const px = (f: number) => X0 + f * W;
  const height = TOP + t.rows.length * STEP + (t.test ? STEP + 8 : 0);
  const testY = TOP + t.rows.length * STEP + 8;
  return (
    <figure className="v7-goal-timeline">
      <svg viewBox={`0 0 760 ${height}`} role="img" aria-label={timelineSummary(design)}>
        <text className="v7-goal-axis" x={px(t.trainEnd / 2)} y={16} textAnchor="middle">training rows · {design.trainRows.toLocaleString("en-GB")}</text>
        {t.test ? (
          <>
            <text className="v7-goal-axis" x={px(1)} y={16} textAnchor="end">final test · {design.testRows.toLocaleString("en-GB")} rows</text>
            <line className="v7-goal-divider" x1={px(t.trainEnd)} y1={22} x2={px(t.trainEnd)} y2={height} />
          </>
        ) : null}
        {t.rows.map((row, i) => {
          const y = TOP + i * STEP;
          return (
            <g key={row.fold}>
              <text className="v7-goal-label" x={X0 - 10} y={y + 15} textAnchor="end">Fold {row.fold}</text>
              {row.learn.map((seg) => <rect key={seg.x} className="v7-goal-learn" x={px(seg.x)} y={y} width={seg.w * W} height={ROW} rx={4} />)}
              <rect className="v7-goal-check" x={px(row.check.x)} y={y} width={row.check.w * W} height={ROW} rx={4} />
              {row.learn[0] && row.learn[0].w * W > 48 ? <text className="v7-goal-in" x={px(row.learn[0].x) + 8} y={y + 15}>learn</text> : null}
              {row.check.w * W > 48 ? <text className="v7-goal-in" x={px(row.check.x + row.check.w / 2)} y={y + 15} textAnchor="middle">check</text> : null}
            </g>
          );
        })}
        {t.test ? (
          <g>
            <text className="v7-goal-label" x={X0 - 10} y={testY + 15} textAnchor="end">Final test</text>
            <rect className="v7-goal-test" x={px(t.test.x)} y={testY} width={t.test.w * W} height={ROW} rx={4} />
            {t.test.w * W > 76 ? <text className="v7-goal-in" x={px(t.test.x + t.test.w / 2)} y={testY + 15} textAnchor="middle">used once</text> : null}
          </g>
        ) : null}
      </svg>
      <figcaption className="muted">
        <span aria-hidden="true" className="v7-goal-key v7-goal-learn-key">■</span> learns from · <span aria-hidden="true" className="v7-goal-key v7-goal-check-key">■</span> checked on · <span aria-hidden="true">□</span> final test set (used once per run, at the end).
        {" "}Drawn to scale by row count.
        {design.kind && design.kind !== "time" ? " The folds are spread through the training rows; each fold's check rows are drawn as one block." : ""}
        {design.testKind && design.testKind !== "time" && t.test ? " The final test rows were set aside before any model was fitted and are drawn at the end." : ""}
        {t.capped ? " Only the first 10 folds are drawn." : ""}
        {!t.rows.length ? " The rows of each fold could not be verified, so the folds are not drawn." : ""}
      </figcaption>
    </figure>
  );
}

/** Goal page for one test design (`/projects/{id}/splits/{nodeId}`). */
export function GoalTestDesign({ projectId, nodeId }: { projectId: string; nodeId: string }) {
  const { graph, node, experimentId, experiment, build } = useBackingRun(projectId, "split_plan", nodeId);
  const plan = useSplitPlan(nodeId);
  const specs = useProblemSpecs(projectId);
  const specId = graph.data && experimentId ? problemSpecOf(graph.data.edges, experimentId) : null;
  const listed = specId ? specs.data?.find((s) => s.id === specId) : specs.data?.find((s) => s.is_current) ?? specs.data?.[0];
  const single = useProblemSpec(projectId, specId && specs.data && !listed ? specId : null);
  const spec = listed ?? single.data ?? null;
  const profile = useDatasetProfile(plan.data?.dataset_id);
  const facts = useMemo(() => goalFacts(build.data, experiment.data), [build.data, experiment.data]);
  // The positive share is counted on the training rows of the profile's test design; used only when it is this one.
  const targetRate = profile.data?.split_plan?.id === nodeId ? profile.data.target_rate ?? null : null;
  const cards = goalCards(spec, facts, { targetRate, clean });
  const design = plan.data ? planDesign(plan.data) : null;
  const why = design ? whyFromRows(design, (v) => plainText(v, 60)) : null;
  if (graph.isError) return <QueryNotice error={graph.error} what="project" />;
  if (graph.isPending) return <p role="status">Loading the goal and test design…</p>;
  if (!node && plan.isError) return <Banner tone="warn">This test design is not in the active workspace, or you cannot see it. Open it from the History page.</Banner>;
  const runHref = experimentId ? safeInternalHref(inspectorPath(projectId, "experiment", experimentId) ?? "") : null;
  const specLoading = specs.isPending || (single.isPending && single.fetchStatus !== "idle");
  return (
    <>
      <Guide />
      {node?.stale ? <Banner tone="warn">This test design was made for a data version that is no longer in use, so it is built on old data.</Banner> : null}
      {specs.isError ? <QueryNotice error={specs.error} what="goal" /> : null}
      {single.isError ? <QueryNotice error={single.error} what="goal of this run" /> : null}
      {experiment.isError ? <QueryNotice error={experiment.error} what="the run" /> : null}
      {specLoading ? <p role="status">Loading the goal…</p> : <GoalCardsView cards={cards} ruleCheck={facts.businessRule} />}
      <p className="muted">
        {specId ? <>The goal of {runHref ? <Link href={runHref}>the newest run on this test design</Link> : "the newest run on this test design"}</> : "The goal in use; no run on this test design is loaded"}
        {spec ? ` (goal version ${spec.version}${spec.status === "draft" ? ", draft" : ""}).` : "."}
      </p>
      <Card title="How we test" aside="the same design is used for every run on it">
        {plan.isError ? <QueryNotice error={plan.error} what="test design" /> : null}
        {plan.isPending ? <p role="status">Loading the test design…</p> : null}
        {design ? (
          <>
            <TestTimeline design={design} />
            <ul className="v7-goal-chips" aria-label="Rows of the test design">
              {designChips(design).map((chip) => <li key={chip.key}><b>{chip.title}</b><small>{chip.detail}</small></li>)}
            </ul>
            {why ? <p><b>Why this design?</b> {why}</p> : null}
          </>
        ) : null}
      </Card>
      <Card title="Who decided" aside="every part of the goal">
        <p className="muted">For the project&apos;s current goal and newest test design; it may differ from the test design shown here.</p>
        <WhoDecided projectId={projectId} />
      </Card>
      <details>
        <summary>Details</summary>
        <KeyValue items={[
          { key: "id", label: "Test design id", value: <span className="mono">{nodeId}</span> },
          { key: "created", label: "Created", value: formatWhen(plan.data?.created_at ?? node?.created_at) },
          { key: "locked", label: "Locked", value: plan.data ? `Yes, ${formatWhen(plan.data.locked_at)}` : "—" },
          ...(design?.timeColumn ? [{ key: "time", label: "Ordered by", value: <span className="mono">{plainText(design.timeColumn, 80)}</span> }] : []),
          ...(design?.groupColumn ? [{ key: "group", label: "Kept together by", value: <span className="mono">{plainText(design.groupColumn, 80)}</span> }] : []),
          ...(plan.data ? [{ key: "strat", label: "Keeps the class mix", value: plan.data.stratified ? "yes" : "no" }] : []),
        ]} />
        {design?.perFold.length ? <p className="muted">Rows per fold (learns from / checked on): {design.perFold.map((f) => `fold ${f.fold}: ${f.learn.toLocaleString("en-GB")} / ${f.check.toLocaleString("en-GB")}`).join(" · ")}.</p> : null}
      </details>
    </>
  );
}

/**
 * Goal page before any test design exists (`/projects/{id}/goal`): the goal from the problem spec, honestly without a test
 * design. `designKnown` false: whether a test design exists could not be read, so nothing is claimed about it.
 */
export function GoalOverview({ projectId, designKnown }: { projectId: string; designKnown: boolean }) {
  const specs = useProblemSpecs(projectId);
  const spec = specs.data?.find((s) => s.is_current) ?? specs.data?.[0] ?? null;
  const experiments = projectHref(projectId, "experiments");
  const newRun = experiments ? safeInternalHref(`${experiments}/new`) : null;
  const start = newRun ? <Link href={newRun}>Start a run</Link> : null;
  return (
    <>
      <Guide />
      {specs.isError ? <QueryNotice error={specs.error} what="goal" /> : null}
      {specs.isPending ? <p role="status">Loading the goal…</p> : null}
      {specs.data && !spec ? (
        <div className="empty">No goal yet. You choose what to predict and what good looks like when you start the first run. {start}</div>
      ) : null}
      {spec ? (
        <>
          <GoalCardsView cards={goalCards(spec, goalFacts(undefined, null), { clean })} />
          <p className="muted">{spec.is_current ? "The goal in use" : "Newest goal (not yet in use)"} (goal version {spec.version}{spec.status === "draft" ? ", draft" : ""}).</p>
        </>
      ) : null}
      {specs.data && !designKnown ? (
        <Card title="How we test">
          <p>Whether this project has a test design could not be checked. Reload the page to try again.</p>
        </Card>
      ) : null}
      {specs.data && designKnown ? (
        <Card title="How we test">
          <p>No test design yet. It is made when the first run starts: DCLab sets aside a final test set, used once per run at the end, before any model is fitted, then cuts the training rows into cross-validation folds. {start}</p>
        </Card>
      ) : null}
      {spec ? (
        <Card title="Who decided" aside="every part of the goal">
          <WhoDecided projectId={projectId} />
        </Card>
      ) : null}
    </>
  );
}
