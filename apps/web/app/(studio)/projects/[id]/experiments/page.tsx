"use client";

import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { GlossaryTerm } from "@/components/studio/GlossaryTerm";
import { Term } from "@/components/studio/Term";
import { QueryNotice, statusTone } from "@/app/components/studio-app/StudioParts";
import { useProjectExperiments, type StudioExperimentItem } from "@/lib/application";
import { COMPARE_MAX, COMPARE_MIN, compareHref } from "@/lib/application/studio-compare";
import { isUuid, plainText } from "@/lib/application/command-search";
import { FINDINGS_BADGE_CAP, useFindingsBadges } from "@/lib/application/studio-data-hooks";
import { RUN_LIST_CAP, useRunDetails } from "@/lib/application/studio-inspect-hooks";
import { designLabels, runName, runOrdinals } from "@/lib/application/studio-names";
import {
  STATUS_FILTERS, bestRunIds, beatsText, changeSentences, familyLabel, matchesStatusFilter, runFacts, scoreText, statusWords, trustCounts, trustSentence,
  type StatusFilter,
} from "@/lib/application/studio-runs";

function ExperimentsPageInner() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const preselect = useSearchParams().get("select");
  const [selected, setSelected] = useState<string[]>(() => (isUuid(preselect) ? [preselect.toLowerCase()] : []));
  const [filter, setFilter] = useState<StatusFilter>("all");
  const experiments = useProjectExperiments(id);
  const allRuns = experiments.data?.items ?? [];
  const ordinals = runOrdinals(allRuns);
  const partial = !!experiments.data?.next_cursor;
  const nameOf = (e: StudioExperimentItem) => runName(ordinals.get(e.id), e.intent ? plainText(e.intent, 80) : e.intent, partial ? e.id.slice(0, 8) : undefined);
  const parentName = (parentId: string) => (ordinals.has(parentId) && !partial ? `Run ${ordinals.get(parentId)}` : `Run ${parentId.slice(0, 8)}`);
  const designs = designLabels([...allRuns].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((e) => e.split_plan_id));
  const completedIds = allRuns.filter((e) => e.status === "completed").map((e) => e.id);
  const badges = useFindingsBadges(completedIds);
  const details = useRunDetails(completedIds);
  const toggle = (experimentId: string) => setSelected((all) => (all.includes(experimentId) ? all.filter((x) => x !== experimentId) : all.length < COMPARE_MAX ? [...all, experimentId] : all));
  const href = compareHref(id, selected);
  const factsOf = (e: StudioExperimentItem) => runFacts(details.get(e.id)?.data);
  // "Best" is claimed only when every finished run was read and scored; otherwise nothing is marked.
  const allRead = completedIds.length > 0 && completedIds.length <= RUN_LIST_CAP && !partial && completedIds.every((x) => details.get(x)?.isSuccess);
  const scored = allRuns.map((e) => ({ id: e.id, completed: e.status === "completed", splitPlanId: e.split_plan_id, metricKey: factsOf(e).metricKey, score: factsOf(e).score, spread: factsOf(e).spread, baseline: factsOf(e).baseline }));
  const ranking = allRead ? bestRunIds(scored) : { ids: new Set<string>(), withinSpread: false };
  const best = ranking.ids;
  // Scores of different metrics are not on one scale: the score column sorts only when every scored run uses the same score.
  const metrics = new Set(scored.filter((r) => r.score !== null).map((r) => r.metricKey));
  const comparable = scored.filter((r) => r.completed && r.score !== null && !r.baseline).length;
  const columns: Column<StudioExperimentItem>[] = [
    {
      key: "pick", header: "Compare",
      render: (e) => (
        <input type="checkbox" checked={selected.includes(e.id)} onChange={() => toggle(e.id)}
          aria-label={`Select ${nameOf(e)} to compare`} disabled={e.status !== "completed"} />
      ),
    },
    {
      key: "experiment", header: "Run", sortValue: (e) => ordinals.get(e.id) ?? 0,
      render: (e) => (
        <>
          <Link href={`/projects/${id}/experiments/${e.id}`}>{nameOf(e)}</Link>
          {best.has(e.id) ? <> <Pill tone="det">Best on cross-validation</Pill>{ranking.withinSpread ? <span className="muted"> (the lead is within the fold-to-fold spread)</span> : null}</> : null}
        </>
      ),
    },
    { key: "model", header: "Model", render: (e) => (e.status === "completed" ? familyLabel(factsOf(e).family) ?? (details.get(e.id)?.isPending ? "…" : "—") : "—") },
    {
      key: "change", header: "What changed",
      render: (e) => {
        if (!e.parent_experiment_id) return "Started from scratch";
        const lines = changeSentences(details.get(e.id)?.data?.change_set);
        const base = `Based on ${parentName(e.parent_experiment_id)}`;
        if (lines.length) return <>{base}: {lines.join("; ")}</>;
        return e.has_change_set ? `${base}, with changes (open the run to see them)` : base;
      },
    },
    {
      key: "score", header: "Cross-validation score ± spread", numeric: true, sortValue: metrics.size <= 1 ? (e) => factsOf(e).score ?? -Infinity : undefined,
      render: (e) => {
        if (e.status !== "completed") return "—";
        const facts = factsOf(e);
        const text = scoreText(facts);
        if (text) return <>{best.has(e.id) ? <b>{text}</b> : text}{facts.metric ? <span className="muted"> {facts.metric}</span> : null}</>;
        const result = details.get(e.id);
        return <span className="muted">{result?.isPending ? "…" : result?.isError ? "unavailable" : result ? "not recorded" : "open run"}</span>;
      },
    },
    { key: "beats", header: "Beats the baseline?", render: (e) => (e.status === "completed" && details.get(e.id)?.isSuccess ? beatsText(factsOf(e)) : "—") },
    {
      key: "findings", header: "Trust checks",
      render: (e) => {
        if (e.status !== "completed") return "—";
        const result = badges.get(e.id);
        if (!result) return <span className="muted" title={`Trust checks are loaded for the first ${FINDINGS_BADGE_CAP} finished runs; open the run to see them.`}>open run</span>;
        if (result.isPending) return <span className="muted">…</span>;
        if (result.isError) return <span className="muted">unavailable</span>;
        const c = trustCounts(result.data);
        if (c.state === "not_checked") return <span className="muted">Not checked yet</span>;
        return (
          <Link href={`/projects/${id}/experiments/${e.id}#findings`} aria-label={`${nameOf(e)}: ${trustSentence(c)}`}>
            <Pill tone="ok">{c.pass} ✓</Pill>
            {c.warn ? <> <Pill tone="warn">{c.warn} ⚠</Pill></> : null}
            {c.fail ? <> <Pill tone="crit">{c.fail} failed</Pill></> : null}
            {c.notChecked ? <> <Pill tone="gray">{c.notChecked} not checked</Pill></> : null}
          </Link>
        );
      },
    },
    { key: "status", header: "Status", sortValue: (e) => e.status, render: (e) => <Pill tone={statusTone(e.status)}>{statusWords(e.status)}</Pill> },
    { key: "split", header: "Test design", render: (e) => (e.split_plan_id ? designs.get(e.split_plan_id) ?? "—" : "—") },
  ];
  const items = allRuns.filter((e) => matchesStatusFilter(e.status, filter));
  const live = allRuns.filter((e) => e.status === "running" || e.status === "queued").length;
  return (
    <>
      <PageHead
        title="Experiments"
        actions={(
          <>
            <button type="button" className="btn" disabled={selected.length < COMPARE_MIN || !href} onClick={() => href && router.push(href)}>Compare selected ({selected.length})</button>
            <Link className="btn primary" href={`/projects/${id}/experiments/new`}>New run</Link>
          </>
        )}
        subtitle="Every run of this project. A run based on another keeps its test design, so the two can be compared fairly. The best run is chosen on cross-validation only; the final test set is scored once per run, for that run's chosen model."
      />
      <PageGuide
        purpose="See every run of this project, what each one changed and how it scored."
        howTo={<>Open a run to follow its steps. Runs on the same <Term definition="How the rows are split into training folds and a final test set. Runs with the same test design are scored on the same rows, so they can be compared.">test design</Term> are comparable. Scores are <GlossaryTerm term="cv" /> scores; the ± is how much they moved from fold to fold. Beating the <GlossaryTerm term="baseline" /> means doing better than a model that ignores the columns.</>}
        youGet="Model, what changed from the run it is based on, score, baseline result, trust checks (✓ passed, ⚠ to review, “failed” for a failed check) and status. Tick two or more finished runs and press Compare selected; open a run to try a change, stop it or put its model in use. Start a new run with New run."
      />
      {experiments.isError ? <QueryNotice error={experiments.error} what="experiment list" /> : null}
      {experiments.isPending ? <p role="status">Loading runs…</p> : null}
      {experiments.data ? (
        <>
          <div className="toolbar">
            <label className="field"><span>Show</span>
              <select value={filter} onChange={(event) => setFilter(event.target.value as StatusFilter)}>
                {STATUS_FILTERS.map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
              </select>
            </label>
            <p className="muted" role="status">{items.length === allRuns.length ? `${allRuns.length} runs` : `${items.length} of ${allRuns.length} runs`}{live ? ` · ${live} in progress` : ""}{partial ? " · showing the newest 100" : ""}</p>
          </div>
          <DataTable caption="Runs" columns={columns} rows={items} rowKey={(e) => e.id} highlightRow={(e) => best.has(e.id)}
            emptyMessage={allRuns.length ? "No run matches this filter." : "No runs yet. Start the first run with New run."} />
          {comparable >= 2 && best.size === 0 && allRead ? <p className="muted">No run is marked best: these runs were scored on different test designs or different scores, so they cannot be ranked against each other.</p> : null}
          {!allRead && completedIds.length > RUN_LIST_CAP ? <p className="muted">More than {RUN_LIST_CAP} finished runs: no run is marked best, because only the first {RUN_LIST_CAP} are read here.</p> : null}
          {!allRead && completedIds.length > 0 && completedIds.length <= RUN_LIST_CAP && partial ? <p className="muted">Only the newest 100 runs are listed, so no run is marked best.</p> : null}
          {!allRead && completedIds.length > 0 && completedIds.length <= RUN_LIST_CAP && !partial ? <p className="muted" role="status">A run is marked best once every finished run has been read.</p> : null}
        </>
      ) : null}
    </>
  );
}

export default function ExperimentsPage() {
  return <Suspense fallback={<p role="status">Loading runs…</p>}><ExperimentsPageInner /></Suspense>;
}
