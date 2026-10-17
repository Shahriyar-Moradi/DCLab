"use client";

/**
 * Guided Improve (V7-B4, option B of ADR 0007), the "Try one change yourself" tab since D4e. A guided flow over existing pieces, in
 * plain words: goal and model in use, the threshold card, "try another model family" (an ordinary new run that adds one family), and
 * the verdict of each try. Nothing on this tab runs unattended: each try starts when a person presses the button, and it spends one
 * look at the final test set. Sealed improvement-loop tries (which never score the final test set) are left out here; they are shown
 * on the Improvement loop tab. The server enforces who may start a try; hiding the form for viewers is only a convenience.
 * Names, reasons and change texts are untrusted and rendered as text. No final test set value is read on this page.
 */
import Link from "next/link";
import { useRef, useState } from "react";
import { ChampionPanel } from "@/app/components/studio-app/CompareBranch";
import { OperatingPointsPanel } from "@/app/components/studio-app/OperatingPoints";
import { safeInternalHref } from "@/components/studio/safe-href";
import { useExperimentFindings } from "@/lib/application/studio-data-hooks";
import { ruleSuggestions } from "@/lib/application/studio-findings";
import { QueryNotice, formatWhen, statusTone } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { GlossaryTerm } from "@/components/studio/GlossaryTerm";
import { KeyValue } from "@/components/studio/KeyValue";
import { PageGuide } from "@/components/studio/PageGuide";
import { PageHead } from "@/components/studio/PageHead";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { useModelBuild, useProjectExperiments, useProjectRefs } from "@/lib/application";
import { projectHref } from "@/lib/application/command-search";
import { createBranch, useWriteInvalidation } from "@/lib/application/studio-compare-hooks";
import { branchProblem } from "@/lib/application/studio-compare";
import { useExperimentDetail, useModelVersionRead, useRunDetails } from "@/lib/application/studio-inspect-hooks";
import { candidateRows } from "@/lib/application/studio-inspect";
import { goalFacts, metricInfo, ruleStatusText } from "@/lib/application/studio-goal";
import { useVerdicts } from "@/lib/application/studio-improve-hooks";
import {
  KEEP_CURRENT_NOTE, LOOK_EXPLAINED, NOISE_NOTE, NOTHING_UNATTENDED, SAME_FOLDS_NOTE, SPEND_NOTE, STANDING_BANNER, VERDICT_CAP, VIEWER_NOTE,
  comparisonsText, countsSentence, defaultIntent, differenceText, familyChoices, foldViews, type FoldView, intentText, lookText, marginText, maxLooks, maxPlanRuns, triesCaveat,
  triesOf, sealedTriesOf, LOOP_TRIES_NOTE, tryBody, newModelsText, shownVerdict, verdictBadge, verdictCounts, verdictKey, verdictSentence, type VerdictLike,
} from "@/lib/application/studio-improve";
import { modelName, runName, runOrdinals } from "@/lib/application/studio-names";
import { runRef } from "@/lib/application/studio-model";
import { changeSentences, familyLabel, runFacts, scoreText, startedByPhrase, statusWords } from "@/lib/application/studio-runs";
import { useSession } from "@/lib/application/session-provider";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

function TryForm({ projectId, parentId, taskType, tried, disabledReason, readFailed }: {
  projectId: string; parentId: string; taskType: string | null | undefined; tried: Array<string | null | undefined>; disabledReason: string | null; readFailed: boolean;
}) {
  const invalidate = useWriteInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const choices = familyChoices(taskType, tried);
  const [family, setFamily] = useState("");
  const [intent, setIntent] = useState("");
  const [edited, setEdited] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const [started, setStarted] = useState<string | null>(null);
  if (disabledReason) return <p className="muted" role="note">{disabledReason}</p>;
  if (readFailed) return <p className="muted" role="note">The run in use could not be read, so the model families to try cannot be listed. Reload the page; if it persists, use New run on the Experiments page.</p>;
  if (choices.length === 0) return <p className="muted" role="note">The kind of answer this run predicts is not recorded, so the model families to try cannot be listed. Use New run on the Experiments page instead.</p>;
  const chosen = choices.some((c) => c.value === family) ? family : "";
  const body = tryBody(chosen, intent || (chosen ? defaultIntent(chosen) : ""));
  const send = async () => {
    if (flight.current || typeof body === "string") return;
    flight.current = true;
    setBusy(true); setError(null); setStarted(null);
    try {
      const created = await createBranch({ parentId, body, key: keys.keyFor("try", `${parentId}:${JSON.stringify(body)}`) });
      keys.done("try");
      setStarted(created.id);
      setFamily(""); setIntent(""); setEdited(false);
      invalidate();
    } catch (caught) {
      setError(branchProblem(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  const openHref = started ? projectHref(projectId, "experiments", started) : null;
  return (
    <form className="form" onSubmit={(event) => { event.preventDefault(); void send(); }} aria-label="Try another model family">
      <label className="field"><span>Model family to add</span>
        <select value={chosen} disabled={busy} onChange={(e) => { setFamily(e.target.value); if (!edited) setIntent(e.target.value ? defaultIntent(e.target.value) : ""); }}>
          <option value="">Choose a family…</option>
          {choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
        </select>
      </label>
      <label className="field"><span>Why are you trying it? (saved with the run)</span>
        <textarea rows={2} maxLength={2000} value={intent} disabled={busy} onChange={(e) => { setIntent(e.target.value); setEdited(true); }} />
      </label>
      <p className="muted">This app does not judge your choice. The server refuses a family that is not installed or that the run in use already trained, and then nothing is started.</p>
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
      {started ? (
        <Banner tone="info">
          A new run was started. It trains again now; its verdict appears in step 3 when it finishes. {openHref ? <Link href={openHref}>Open the new run</Link> : null}
        </Banner>
      ) : null}
      <div className="toolbar">
        <button type="submit" className="btn primary" disabled={busy || typeof body === "string"}>{busy ? "Starting…" : "Start this try (uses one look at the final test set)"}</button>
      </div>
      {typeof body === "string" && family ? <p className="muted" role="status">{body}</p> : null}
    </form>
  );
}

function Folds({ v, metric, name }: { v: VerdictLike; metric: string; name: string }) {
  const columns: Column<FoldView>[] = [
    { key: "fold", header: "Fold", numeric: true, render: (r) => r.fold },
    { key: "current", header: `Run in use (${metric})`, numeric: true, render: (r) => r.current },
    { key: "try", header: `This try (${metric})`, numeric: true, render: (r) => r.tried },
    { key: "diff", header: "Difference (this try − run in use)", numeric: true, render: (r) => r.difference },
    { key: "words", header: "Which way", render: (r) => r.words },
  ];
  return <DataTable caption={`Fold by fold scores of ${name}`} columns={columns} rows={foldViews(v)} rowKey={(r) => String(r.fold)} />;
}

/** `embedded`: shown as the "Try one change yourself" tab under the Improve page heading, so it draws no heading of its own. */
export function ImproveScreen({ projectId, embedded = false }: { projectId: string; embedded?: boolean }) {
  const { user } = useSession();
  const canRun = hasCapability(user, CAPABILITIES.workspaceExecuteMl);
  const refs = useProjectRefs(projectId);
  const championId = refs.data?.items.find((r) => r.ref_kind === "champion_model")?.target.id ?? null;
  const model = useModelVersionRead(championId);
  const inProject = !!model.data && model.data.project_id === projectId;
  const runInUseId = inProject ? model.data!.lineage.experiment_id : null;
  const detail = useExperimentDetail(runInUseId);
  const build = useModelBuild(detail.data?.workspace_id, runInUseId ?? undefined);
  const runs = useProjectExperiments(projectId);
  const allRuns = runs.data?.items ?? [];
  const partial = !!runs.data?.next_cursor;
  const ordinals = runOrdinals(allRuns);
  const tries = triesOf(allRuns, runInUseId);
  const sealedTries = sealedTriesOf(allRuns, runInUseId);
  const finished = tries.filter((t) => t.status === "completed").slice(0, VERDICT_CAP);
  const finishedIds = finished.map((t) => t.id);
  const verdicts = useVerdicts(finishedIds);
  // The run in use has its own place among the looks even when no try has finished (its verdict read has no parent to compare with, but carries the counts).
  const ownLook = useVerdicts(runInUseId ? [runInUseId] : []).get(runInUseId ?? "");
  const details = useRunDetails(finishedIds);
  const findings = useExperimentFindings(runInUseId);
  const experimentsHref = projectHref(projectId, "experiments");
  const modelsHref = projectHref(projectId, "models");

  const head = (
    <>
      {embedded ? null : <PageHead title="Improve" subtitle="Try another model family, see whether the gain is bigger than the noise, and keep the current model when it is not." />}
      <PageGuide
        purpose="A guided way to ask: can this project's model be made better, and is the gain bigger than the noise on cross-validation?"
        howTo={<>Check the goal, optionally pick a different <GlossaryTerm term="threshold" /> (step 1), start a try with one more model family (step 2), then read whether it beat the noise (step 3). {NOTHING_UNATTENDED}</>}
        youGet="For each try: its cross-validation score, how much it differs from the model in use, the noise margin, and which look at the final test set it was."
        attention={<>{STANDING_BANNER} {SPEND_NOTE}</>}
      />
      <Banner tone="warn">{STANDING_BANNER}</Banner>
    </>
  );

  if (refs.isError) return <>{head}<QueryNotice error={refs.error} what="versions in use" /></>;
  if (refs.isPending) return <>{head}<p role="status">Loading the model in use…</p></>;
  if (!championId) {
    return (
      <>
        {head}
        <div className="empty" role="note">
          <p><b>No model is in use yet.</b></p>
          <p>Improve starts from the model in use. Finish a run, then put its model in use from the run&apos;s page.{experimentsHref ? <> <Link href={experimentsHref}>Open experiments</Link></> : null}</p>
        </div>
      </>
    );
  }
  if (model.isError) return <>{head}<QueryNotice error={model.error} what="model in use" /></>;
  if (!model.data) return <>{head}<p role="status">Loading the model in use…</p></>;
  if (!inProject || !runInUseId) {
    return <>{head}<Banner tone="warn">The model in use could not be matched to a run of this project, so nothing can be improved from here.</Banner></>;
  }

  const facts = runFacts(detail.data);
  const goal = goalFacts(undefined, detail.data);
  const built = runRef(ordinals, partial, runInUseId);
  const runHref = projectHref(projectId, "experiments", runInUseId);
  const modelHref = projectHref(projectId, "models", model.data.id);
  const familyText = familyLabel(model.data.family ?? model.data.algorithm ?? facts.family);
  const score = scoreText(facts);
  const triedFamilies = build.data ? candidateRows(build.data).flatMap((c) => [c.family, c.algorithm]) : [];
  const readVerdicts = finishedIds.map((id) => verdicts.get(id)?.data ?? null);
  const counts = verdictCounts(readVerdicts);
  const allRead = finishedIds.every((id) => verdicts.get(id)?.isSuccess);
  const summary = allRead && finishedIds.length > 0 ? countsSentence(counts) : null;
  const planRuns = maxPlanRuns(readVerdicts);
  const caveat = triesCaveat(planRuns);
  const looks = maxLooks([...readVerdicts, ownLook?.data ?? null]);
  const nameOf = (id: string, intent: string | null | undefined) => runName(ordinals.get(id), intentText(intent), partial ? id.slice(0, 8) : undefined);

  return (
    <>
      {head}

      <Card title="Your goal and the model in use" aside={<Pill tone="ok">★ in use</Pill>}>
        {detail.isError ? <QueryNotice error={detail.error} what="run in use" /> : null}
        <KeyValue items={[
          { key: "model", label: "Model in use", value: <>{modelHref ? <Link href={modelHref}>{modelName(model.data.version)}</Link> : modelName(model.data.version)}{familyText ? `, ${familyText}` : ""}, built by {runHref ? <Link href={runHref}>{built}</Link> : built}</> },
          { key: "target", label: "What it predicts", value: detail.isPending ? "Loading…" : detail.isError ? "Could not be read" : goal.target ?? "Not recorded" },
          { key: "task", label: "Kind of answer", value: detail.isPending ? "Loading…" : detail.isError ? "Could not be read" : goal.task ?? "Not recorded" },
          { key: "metric", label: "Models are ranked on", value: detail.isPending ? "Loading…" : detail.isError ? "Could not be read" : goal.metric?.label ?? "Not recorded" },
          {
            key: "look", label: "Final test set",
            value: ownLook?.isPending ? "Loading…" : ownLook?.isError ? "Look count could not be read" : <>{lookText(ownLook?.data?.test_set_looks)} <span className="muted">(this run in use)</span></>,
          },
          { key: "rule", label: "Business rule", value: detail.isPending ? "Loading…" : detail.data ? ruleStatusText(detail.data.metrics?.constraint_status) : "Unavailable" },
          {
            key: "score", label: <><GlossaryTerm term="cv" /> score ± spread</>,
            value: detail.isPending ? "Loading…" : detail.isError ? "Could not be read" : score ? <>{score}{facts.metric ? <span className="muted"> {facts.metric}</span> : null} <span className="muted">(the ± is how much it moved from fold to fold)</span></> : <span className="muted">Not recorded</span>,
          },
        ]} />
        <p className="muted">These numbers come from cross-validation on the training rows. They are in the score&apos;s own units. {NOISE_NOTE}</p>
      </Card>

      <div id="operating-points">
        <Card title="Step 1 · Set the threshold" aside={<Pill tone="det">no retraining</Pill>}>
          <p className="muted">
            A different threshold changes how many rows get flagged without training anything again. Your choice is saved in History as a decision only: scoring keeps the model&apos;s locked threshold,
            and applying a chosen threshold needs a new model version.
          </p>
          <OperatingPointsPanel projectId={projectId} experimentId={runInUseId} />
        </Card>
      </div>

      <div id="suggested">
        <Card title="Suggested by the rules" aside={<Pill tone="det">from the trust checks, no AI</Pill>}>
          <p>Trust checks of the run in use that did not pass and have a change you can try. Each opens the change form on the run in use&apos;s page with the change and its reason filled in; nothing starts until you choose Start there, and each try spends one look at the final test set.</p>
          {findings.isPending ? <p role="status">Reading the trust checks of the run in use…</p>
            : findings.isError ? <QueryNotice error={findings.error} what="trust checks of the run in use" />
              : !findings.data.investigated ? <p className="muted" role="note">The trust checks of the run in use are not recorded yet, so the rules suggest nothing.</p>
                : (() => {
                  const suggestions = ruleSuggestions(projectId, runInUseId, findings.data);
                  if (suggestions.length === 0) return <div className="empty" role="note"><p>The rules suggest no change: no trust check of the run in use asks for one. Keeping the current model is a normal result.</p></div>;
                  return (
                    <ul className="plain-list" aria-label="Changes suggested by the rules">
                      {suggestions.map((sug) => {
                        const href = safeInternalHref(sug.href);
                        return (
                          <li key={sug.check} className="card flat" data-suggestion={sug.check}>
                            <p className="toolbar"><Pill tone={sug.tone}>{sug.status}</Pill> <b>{sug.label}</b> <span className="muted">Suggested by: the rules</span></p>
                            <p>{sug.what}</p>
                            <p className="small muted">Reason filled in: &ldquo;{sug.reason}&rdquo;</p>
                            {canRun ? (href ? <p><Link className="btn" href={href}>Try this change</Link></p> : null) : <p className="muted" role="note">{VIEWER_NOTE}</p>}
                          </li>
                        );
                      })}
                    </ul>
                  );
                })()}
        </Card>
      </div>

      <div id="try">
        <Card title="Step 2 · Try another model family" aside={<Pill tone="det">each try is a new run</Pill>}>
          <p>{SAME_FOLDS_NOTE} It adds one model family to the run in use; nothing else changes. {SPEND_NOTE}</p>
          <p className="muted">{NOTHING_UNATTENDED} There is no budget, no schedule and no automatic stop.</p>
          {detail.isPending ? <p role="status">Reading the run in use…</p> : (
            <TryForm projectId={projectId} parentId={runInUseId} taskType={detail.data?.task_type} tried={triedFamilies} disabledReason={canRun ? null : VIEWER_NOTE} readFailed={detail.isError} />
          )}
          {detail.data && (build.isError || build.isPending) ? <p className="muted" role="status">{build.isError ? "The families the run in use already trained could not be read, so the list may include some it already has; the app will refuse those." : "Reading the families the run in use already trained…"}</p> : null}
        </Card>
      </div>

      <div id="verdicts">
        <Card title="Step 3 · Is the gain bigger than the noise?" aside={<Pill tone="det">calculated by fixed rules, no AI</Pill>}>
          <p>
            Each try is compared with the run in use on the <b>same folds</b>: the difference in score on each fold, and a{" "}
            <Term definition="How much two scores can differ just by chance, allowing for the folds not being independent. A gain bigger than the margin is unlikely to be luck for this one comparison. It is evidence, not proof, and five folds give little power.">noise margin</Term>.
            {" "}{NOISE_NOTE} Judged on cross-validation only, never on the final test set.
          </p>
          {summary ? <p role="status"><b>{summary}</b></p> : null}
          {caveat ? <p className="muted">{caveat}</p> : null}
          {looks !== null ? <p className="muted">The final test set of this test design has been looked at {looks} {looks === 1 ? "time" : "times"} so far. {LOOK_EXPLAINED}</p> : null}
          {runs.isError ? <QueryNotice error={runs.error} what="experiment list" /> : null}
          {runs.isPending ? <p role="status">Loading the tries…</p> : null}
          {sealedTries > 0 ? <p className="muted" role="note">{LOOP_TRIES_NOTE}</p> : null}
          {runs.data && tries.length === 0 ? <div className="empty" role="note"><p>No tries yet. Start one in step 2; its verdict appears here when it finishes.</p></div> : null}
          {partial ? <p className="muted">Only the newest 100 runs of the project were read, so older tries may be missing here.</p> : null}
          {tries.length > finished.length + tries.filter((t) => t.status !== "completed").length ? <p className="muted">Verdicts are read for the newest {VERDICT_CAP} finished tries only.</p> : null}
          <ul className="plain-list" aria-label="Tries">
            {tries.map((t) => {
              const name = nameOf(t.id, t.intent);
              const href = projectHref(projectId, "experiments", t.id);
              const read = verdicts.get(t.id);
              const run = details.get(t.id);
              const v = read?.data ?? null;
              const changes = changeSentences(run?.data?.change_set);
              const runFactsOf = run?.data ? runFacts(run.data) : null;
              const runScore = runFactsOf ? scoreText(runFactsOf) : null;
              const badge = v ? verdictBadge(shownVerdict(v)) : null;
              const key = v ? verdictKey(shownVerdict(v)) : null;
              const diff = v ? differenceText(v) : null;
              const margin = v ? marginText(v) : null;
              const unit = v ? metricInfo(v.metric)?.label ?? "" : "";
              return (
                <li key={t.id} className="card flat">
                  <h3>{href ? <Link href={href}>{name}</Link> : name} <Pill tone={statusTone(t.status)}>{statusWords(t.status)}</Pill>{badge ? <> <Pill tone={badge.tone}>{badge.label}</Pill></> : null}</h3>
                  <p className="muted">Started {formatWhen(t.created_at)}{startedByPhrase(t.started_by)}. {changes.length ? changes.join("; ") : run?.isPending ? "Reading what changed…" : "What changed is not recorded here; open the run."}</p>
                  {t.status !== "completed" ? (
                    <p>{["running", "queued", "cancelling"].includes(t.status) ? "Still training. The verdict appears when it finishes." : "No verdict: this try did not finish. A run that failed or was stopped after choosing its best model may still count as a look."}</p>
                  ) : !read ? (
                    <p className="muted">Verdicts are read for the newest {VERDICT_CAP} finished tries only. Open the run to see this one.</p>
                  ) : read.isPending ? (
                    <p role="status">Reading the verdict…</p>
                  ) : read.isError ? (
                    <QueryNotice error={read.error} what="verdict" />
                  ) : v ? (
                    <>
                      <p>{verdictSentence(v)}</p>
                      <KeyValue items={[
                        { key: "score", label: "Cross-validation score ± spread", value: runScore ? <>{runScore} <span className="muted">{runFactsOf?.metric ?? ""}</span></> : <span className="muted">{run?.isPending ? "Loading…" : run?.isError ? "Unavailable" : "Not recorded"}</span> },
                        { key: "diff", label: "Difference from the run in use", value: diff ?? "Not available" },
                        { key: "margin", label: "Noise margin", value: margin ? `${margin} ${unit}`.trim() : "Not available" },
                        { key: "comparisons", label: "Comparisons the margin covers", value: comparisonsText(v.comparisons) ?? "Not available" },
                        { key: "added", label: "Models this try added", value: newModelsText(v.new_candidates) ?? "Not available" },
                        { key: "folds", label: "Folds compared (K)", value: typeof v.folds === "number" ? String(v.folds) : "Not available" },
                        { key: "look", label: "Final test set", value: <>{lookText(v.test_set_looks)} <span className="muted">(this try spent one look; counted conservatively)</span></> },
                      ]} />
                      {v.fold_scores && v.fold_scores.length > 0 && key !== "not_comparable" ? (
                        <details><summary>Fold by fold</summary><Folds v={v} metric={unit || "score"} name={name} /></details>
                      ) : null}
                      {key === "beats_the_noise" ? (
                        <>
                          <p>
                            To use this model instead, put it in use here or on {href ? <Link href={href}>the run&apos;s page</Link> : "the run's page"}; DCLab saves that change in History.
                            {" "}Choose it for the cross-validation result, not for its final-test number.
                            {planRuns !== null && planRuns > 2 ? ` With ${planRuns} runs on these folds, one can beat the noise by luck.` : ""}
                          </p>
                          <div aria-label={`Use the model of ${name}`} role="group"><h4>Use this model</h4><ChampionPanel projectId={projectId} experimentId={t.id} compact /></div>
                        </>
                      ) : null}
                    </>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </Card>
      </div>

      <Card title="Keep the current model">
        <p>{KEEP_CURRENT_NOTE}</p>
        {modelsHref ? <p><Link href={modelsHref}>See all models of this project</Link></p> : null}
      </Card>
    </>
  );
}
