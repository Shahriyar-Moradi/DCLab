"use client";

/**
 * "Approved for scoring" (V7-B2-UI): which model the project scores with, who approved it and when, and the one confirm
 * step that approves a model or goes back to an earlier one. Approving is the existing put-in-use write (`makeChampion`:
 * Idempotency-Key, If-Match); a person approves, nothing is retrained. Names and reasons are untrusted plain text.
 */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Card } from "@/components/studio/Card";
import { PageGuide } from "@/components/studio/PageGuide";
import { Pill } from "@/components/studio/Pill";
import { Banner } from "@/components/studio/Banner";
import { QueryNotice, formatWhen } from "@/app/components/studio-app/StudioParts";
import { isUuid, projectHref } from "@/lib/application/command-search";
import { useSession } from "@/lib/application/session-provider";
import { modelRows } from "@/lib/application/studio-card";
import { makeChampion, useWriteInvalidation, type RefVersion } from "@/lib/application/studio-compare-hooks";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { useProjectRefs } from "@/lib/application/studio-hooks";
import { useModelVersionRead } from "@/lib/application/studio-inspect-hooks";
import {
  REASON_MAX, RELEASES_LISTED, approvalSummary, confirmWords, isConflict, mapApprovalError, modelAction, modelLabel, reasonToSend, releaseRows, rowAction,
  targetStillOffered, unapprovedModels, type ApprovalMode,
} from "@/lib/application/studio-releases";
import { useProjectReleases } from "@/lib/application/studio-releases-hooks";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

type Target = { mode: ApprovalMode; modelVersionId: string; model: string; returnId: string };
const SUMMARY_ID = "approval-summary";

/** The one confirm step for approve / go back; the Monitoring screen reuses it for its go-back (one write path). */
export function ConfirmApproval({ projectId, target, current, onClose, onDone, where }: {
  projectId: string; target: Target; current: string | null; onClose: () => void; onDone: (text: string) => void;
  /** Where the change was made, for the History reason when none is typed (default: the Model screen). */
  where?: string;
}) {
  const model = useModelVersionRead(target.modelVersionId);
  const refs = useProjectRefs(projectId);
  const invalidate = useWriteInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  // A retry of the same action resends the same request: its key AND the versions it was checked against.
  const frozen = useRef<{ fingerprint: string; champion: RefVersion; recipe: RefVersion } | null>(null);
  const flight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const words = confirmWords(target.mode, target.model, current);
  const ready = !!model.data && !!refs.data && !refs.isFetching;
  useEffect(() => heading.current?.focus(), []);
  const send = async () => {
    if (flight.current || !model.data || !refs.data || refs.isFetching) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    const m = model.data;
    const text = reasonToSend(target.mode, target.model, reason, where);
    const fingerprint = `${m.id}:${text}`;
    if (frozen.current?.fingerprint !== fingerprint) {
      frozen.current = {
        fingerprint,
        champion: refs.data.items.find((r) => r.ref_kind === "champion_model"),
        recipe: refs.data.items.find((r) => r.ref_kind === "feature_recipe"),
      };
    }
    try {
      await makeChampion({
        projectId, modelVersionId: m.id, candidateId: m.lineage.candidate_id, featureRecipeId: m.lineage.feature_recipe_id ?? null,
        champion: frozen.current.champion, recipe: frozen.current.recipe, rationale: text, key: keys.keyFor("approve", fingerprint),
      });
      keys.done("approve");
      frozen.current = null;
      invalidate();
      onDone(`${target.model} is now the model in use and approved for scoring. The change was saved in History.`);
    } catch (caught) {
      if (isConflict(caught)) { keys.done("approve"); frozen.current = null; } // the same request can never succeed: next try is new
      setError(mapApprovalError(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <form className="form card" aria-label={words.title} onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <h3 ref={heading} tabIndex={-1}>{words.title}</h3>
      <ul>{words.lines.map((line) => <li key={line}>{line}</li>)}</ul>
      {model.isError ? <QueryNotice error={model.error} what="model" /> : null}
      {refs.isError ? <QueryNotice error={refs.error} what="versions in use" /> : null}
      {!ready && !model.isError && !refs.isError ? <p role="status">Loading the model…</p> : null}
      <label className="field"><span>Why (optional, saved with the change)</span>
        <textarea value={reason} rows={2} maxLength={REASON_MAX} disabled={busy} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error ? (
        <Banner tone="crit" actions={<button type="button" className="btn" onClick={() => { invalidate(); setError(null); }}>Reload</button>}><b>{error.title}.</b> {error.detail}</Banner>
      ) : null}
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy || !ready}>{busy ? "Saving…" : words.button}</button>
      </div>
    </form>
  );
}

export function ApprovedForScoring({ projectId }: { projectId: string }) {
  const { user } = useSession();
  const canWrite = hasCapability(user, CAPABILITIES.workspaceExecuteMl);
  const graph = useProjectGraph(projectId);
  const refs = useProjectRefs(projectId);
  const releases = useProjectReleases(projectId);
  const [target, setTarget] = useState<Target | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [focusTo, setFocusTo] = useState<string | null>(null);
  // "In use" is the project's own reference, not the (windowed) list of models in the graph.
  const championId = refs.data?.items.find((r) => r.ref_kind === "champion_model")?.target.id ?? null;
  const models = (graph.data ? modelRows(projectId, graph.data.nodes, graph.data.edges) : []).map((m) => ({ ...m, champion: m.id === championId }));
  const listed = models.find((m) => m.id === championId);
  const championRead = useModelVersionRead(championId && !listed ? championId : null);
  const inUseName = listed ? modelLabel(listed.version) : championRead.data ? modelLabel(championRead.data.version) : "The model in use";
  const inUse = championId && isUuid(championId) ? { id: championId, name: inUseName } : null;
  const items = releases.data?.items ?? [];
  const rows = releaseRows(items, { myUserId: user?.id ?? null, inUseModelId: inUse?.id ?? null, when: formatWhen });
  const summary = approvalSummary({
    pending: releases.isPending || refs.isPending, failed: releases.isError || refs.isError, currentReleaseId: releases.data?.current_release_id, rows, inUse,
  });
  const loaded = !!graph.data && !!releases.data && !!refs.data;
  const unapproved = loaded ? unapprovedModels(models, items) : [];
  const history = projectHref(projectId, "decisions");
  const viewer = { canWrite, confirming: !!target };
  const stale = !!target && loaded && !targetStillOffered(target.modelVersionId, rows, unapproved);

  useEffect(() => { if (stale) setTarget(null); }, [stale]);
  useEffect(() => {
    if (!focusTo) return;
    document.getElementById(focusTo)?.focus();
    setFocusTo(null);
  }, [focusTo]);

  const open = (mode: ApprovalMode, modelVersionId: string, model: string, returnId: string) => { setTarget({ mode, modelVersionId, model, returnId }); setDone(null); };
  const confirmFor = (modelVersionId: string) =>
    target && target.modelVersionId === modelVersionId ? (
      <ConfirmApproval
        key={`${target.mode}:${modelVersionId}`} projectId={projectId} target={target} current={inUse ? inUse.name : null}
        onClose={() => { setFocusTo(target.returnId); setTarget(null); }}
        onDone={(text) => { setDone(text); setTarget(null); setFocusTo(SUMMARY_ID); }}
      />
    ) : null;
  return (
    <>
      <PageGuide
        purpose="See which model the project scores with, who approved it and when, and go back to an earlier one if needed."
        howTo="Read the list, newest first. To change the model in use, press “Approve for scoring” on a model or “Go back to this model” on an earlier one, read what changes, then confirm."
        youGet={`The model in use, the latest ${RELEASES_LISTED} approvals with their date and reason, and a record of each change in History.`}
        attention="Approving does not retrain the model or test it again. Predictions already made are not changed. Other models can still score a file from their own page: only scoring with the model in use is checked against its approval. Weekly scoring, if it is on, uses the model in use only when it is approved: see the Weekly scoring card on the Predictions screen."
      />
      <Card title="Approved for scoring" aside={<Pill tone="ok">Working today</Pill>}>
        {summary.kind === "error" ? (
          releases.isError ? <QueryNotice error={releases.error} what="list of approvals" /> : <QueryNotice error={refs.error} what="versions in use" />
        ) : summary.kind === "loading" ? <p role="status">Loading the approvals…</p> : (
          <p id={SUMMARY_ID} tabIndex={-1} data-testid="approval-summary">{summary.text}</p>
        )}
        <div role="status" aria-live="polite" className={done ? "banner" : undefined}>
          {done ? <>{done}{history ? <> <Link href={history}>Open History</Link>.</> : null}</> : null}
        </div>
        {graph.isError ? <QueryNotice error={graph.error} what="project graph" /> : null}
        {!canWrite ? <p className="muted">Only people who can change this workspace can approve a model or go back to an earlier one.</p> : null}
        {releases.data ? (
          rows.length ? (
            <ul className="approvals" aria-label="Approvals, newest first">
              {rows.map((r) => (
                <li key={r.id} id={`approval-row-${r.id}`} tabIndex={-1}>
                  <p>
                    <b>{r.model}</b>{r.current ? <> <Pill tone="ok">In use now</Pill></> : null}
                    {r.oldFormat ? <> <Pill tone="warn">old saved format: can&apos;t be approved again until trained again</Pill></> : null}
                    <br /><span className="muted">{r.line}</span>
                  </p>
                  <p>{r.reason ? <>Reason: {r.reason}</> : <span className="muted">No reason is shown to you.</span>}</p>
                  <details>
                    <summary>Details<span className="sr-only"> of this approval of {r.model}</span></summary>
                    <p className="muted">Approval <span className="mono">{r.id}</span> · model <span className="mono">{r.modelVersionId}</span> · History entry <span className="mono">{r.historyId}</span></p>
                  </details>
                  {rowAction(r, viewer) ? (
                    <p><button type="button" className="btn" aria-label={`Go back to this model: ${r.model}`} onClick={() => open("go_back", r.modelVersionId, r.model, `approval-row-${r.id}`)}>Go back to this model</button></p>
                  ) : null}
                  {r.goBack ? confirmFor(r.modelVersionId) : null}
                </li>
              ))}
            </ul>
          ) : <p className="muted">No model has been approved for scoring yet.</p>
        ) : null}
      </Card>
      {unapproved.length ? (
        <Card title="Not approved yet">
          <ul className="approvals" aria-label="Models not approved yet">
            {unapproved.map((m) => {
              const name = modelLabel(m.version);
              return (
                <li key={m.id} id={`approve-row-${m.id}`} tabIndex={-1}>
                  <p>
                    {m.href ? <Link href={m.href}>{name}</Link> : name}{m.champion ? <> <Pill tone="ok">in use, no approval recorded</Pill></> : null}
                  </p>
                  {modelAction(viewer) ? (
                    <p><button type="button" className="btn primary" aria-label={`Approve for scoring: ${name}`} onClick={() => open("approve", m.id, name, `approve-row-${m.id}`)}>Approve for scoring</button></p>
                  ) : null}
                  {confirmFor(m.id)}
                </li>
              );
            })}
          </ul>
        </Card>
      ) : null}
    </>
  );
}
