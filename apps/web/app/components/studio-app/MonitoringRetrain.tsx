"use client";

/**
 * Retrain on newest data and editable thresholds on the Monitoring screen (D5c, ADR 0012): the "Retrain on newest data" button
 * and its confirm step, the retrained model beside the model in use (same kind of test, latest months; or the named reason it
 * is not comparable) with the real outcomes of the model in use beside it (not the same data), Switch / Keep through the
 * existing approval of the switch proposal, the retrain policy (Off / Ask me / Automatic) and the thresholds form. Owners and
 * administrators edit the policy and thresholds (the read's `can_edit`); people with ML-write start retrains and decide;
 * everyone else reads. Every value comes from an API field; names are plain text; errors are fixed sentences by code.
 */
import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { KeyValue } from "@/components/studio/KeyValue";
import { Pill } from "@/components/studio/Pill";
import { isUuid, projectHref } from "@/lib/application/command-search";
import { useSession } from "@/lib/application/session-provider";
import { proposedMoves } from "@/lib/application/studio-compare";
import { acceptRefProposal, resolveDecision, useDecisionDetail } from "@/lib/application/studio-compare-hooks";
import { useProjectRefs } from "@/lib/application/studio-hooks";
import { refVersionsFor } from "@/lib/application/studio-inbox";
import {
  FLOORS_NOT_DRIFT, NOT_SAME_DATA, POLICIES, RATIONALE_MAX, RULE_LIMIT, THRESHOLDS_APPLY, WORDING, boundsText, canOfferRetrain, comparisonView, draftFrom,
  isPolicy, mapRetrainError, policyFacts, policyWords, realOutcomesText, retrainConfirmLines, setByText, thresholdChanges, validateThresholds, valuesText,
  type DraftField, type PolicyValue, type ThresholdDraft, type ThresholdValues,
} from "@/lib/application/studio-retrain";
import {
  setRetrainPolicy, setThresholds, startRetrain, useMonitoringThresholds, useRetrainInvalidation, useRetrainPolicy,
  type StudioRetrainStatus, type StudioThresholds,
} from "@/lib/application/studio-retrain-hooks";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { useProjectDatasets } from "@/lib/application/studio-wizard-hooks";
import { ApiError } from "@/lib/infrastructure/api-client";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

function ErrorLine({ error }: { error: PlainError | null }) {
  return error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null;
}

/** A read that failed: "not yours / not here" (403, 404) is distinct from a failure. */
export function RetrainReadFailed({ what, error, onRetry }: { what: string; error: unknown; onRetry: () => void }) {
  const missing = error instanceof ApiError && (error.status === 404 || error.status === 403);
  return (
    <Banner tone={missing ? "warn" : "crit"} actions={<button type="button" className="btn" onClick={onRetry}>Reload</button>}>
      {missing ? <>The {what} is not in the active workspace, or you cannot see it.</> : <><b>The {what} could not be loaded.</b> Nothing was changed. Check your connection and reload.</>}
    </Banner>
  );
}

/** One write in flight at a time, with an Idempotency-Key per action that a retry of the same request reuses. */
function useWrite() {
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const run = async (action: string, fingerprint: string, send: (key: string) => Promise<unknown>, fail: (caught: unknown) => PlainError): Promise<boolean> => {
    if (flight.current) return false;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      await send(keys.keyFor(action, fingerprint));
      keys.done(action);
      return true;
    } catch (caught) {
      setError(fail(caught));
      return false;
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return { busy, error, setError, run };
}

function Done({ text, projectId }: { text: string | null; projectId: string }) {
  const history = projectHref(projectId, "decisions");
  return <div role="status" aria-live="polite" className={text ? "banner" : undefined}>{text ? <>{text}{history ? <> <Link href={history}>Open History</Link>.</> : null}</> : null}</div>;
}

/* ---------- Retrain on newest data (the button under "What the system does on its own") ---------- */

export function RetrainAction({ projectId, status, canStartRuns }: { projectId: string; status: StudioRetrainStatus | undefined; canStartRuns: boolean }) {
  const datasets = useProjectDatasets(projectId);
  const invalidate = useRetrainInvalidation();
  const write = useWrite();
  const [open, setOpen] = useState(false);
  const [done, setDone] = useState<string | null>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (open) heading.current?.focus(); }, [open]);
  if (!status) return null;
  const newer = status.newer_dataset_id ?? null;
  const data = projectHref(projectId, "data");
  if (!canOfferRetrain(status, canStartRuns)) {
    if (status.state === "retraining") return <Done text={done} projectId={projectId} />;
    if (newer && !canStartRuns) return <p className="muted" data-testid="retrain-viewer">A newer labelled file exists. A person who can run ML work in this workspace can retrain on it.</p>;
    if (!newer && canStartRuns) return <p className="muted">To retrain, a person uploads a newer labelled file{data ? <> on the <Link href={data}>Data</Link> screen</> : null}.</p>;
    return <Done text={done} projectId={projectId} />;
  }
  const file = (datasets.data ?? []).find((d) => d.id === newer);
  const sameModel = status.request && status.request.incumbent_model_version_id === status.model_in_use_id;
  const lines = retrainConfirmLines(file ? { name: file.name, rows: file.row_count, uploadedAt: file.created_at } : null, sameModel ? status.request?.not_carried ?? null : null);
  const send = async () => {
    if (!newer || !isUuid(newer)) return;
    const ok = await write.run("retrain", newer, (key) => startRetrain({ projectId, datasetId: newer, key }), (caught) => mapRetrainError(caught, "start"));
    invalidate();
    if (ok) { setOpen(false); setDone("Retrain queued: a new run on the newer labelled file is queued and starts when a worker is free. Its result appears on this screen; switching stays your choice."); }
  };
  return (
    <>
      <Done text={done} projectId={projectId} />
      {open ? (
        <form className="form card" aria-label="Retrain on newest data?" onSubmit={(e) => { e.preventDefault(); void send(); }}>
          <h3 ref={heading} tabIndex={-1}>Retrain on newest data?</h3>
          <ul>{lines.map((line) => <li key={line}>{line}</li>)}</ul>
          <ErrorLine error={write.error} />
          <div className="toolbar">
            <button type="button" className="btn" disabled={write.busy} onClick={() => { setOpen(false); write.setError(null); }}>Cancel</button>
            <button type="submit" className="btn primary" disabled={write.busy}>{write.busy ? "Starting…" : "Yes, retrain on this file"}</button>
          </div>
        </form>
      ) : <p><button type="button" className="btn primary" onClick={() => { setOpen(true); setDone(null); }}>Retrain on newest data</button></p>}
    </>
  );
}

/* ---------- the retrained model beside the model in use; Switch / Keep ---------- */

type Mode = "switch" | "keep";

function Decide({ projectId, proposalId, mode, onClose, onDone }: { projectId: string; proposalId: string; mode: Mode; onClose: () => void; onDone: (text: string) => void }) {
  const detail = useDecisionDetail(mode === "switch" ? proposalId : null);
  const refs = useProjectRefs(mode === "switch" ? projectId : undefined);
  const invalidate = useRetrainInvalidation();
  const write = useWrite();
  const [reason, setReason] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), []);
  const waiting = mode === "switch" && (!detail.data || !refs.data);
  const moves = detail.data ? proposedMoves({ effective_state: "proposed", decision_type: detail.data.decision_type, details: detail.data.details }) : [];
  const unusable = mode === "switch" && !!detail.data && moves.length === 0;
  const send = async () => {
    const rationale = reason.trim();
    if (!rationale || waiting || unusable) return;
    const versions = refs.data ? refVersionsFor(refs.data.items) : null;
    const ok = await write.run(mode, `${proposalId}:${rationale}:${JSON.stringify(versions)}`, (key) => {
      if (mode === "keep") return resolveDecision({ action: "reject", decisionId: proposalId, rationale, key });
      return acceptRefProposal({ projectId, proposalId, moves, refs: refs.data!.items, rationale, key });
    }, (caught) => mapRetrainError(caught, "decide"));
    invalidate();
    if (ok) onDone(mode === "switch" ? "Switched: the retrained model is now the model in use. It is saved in History." : "Kept: the model in use stays. It is saved in History.");
  };
  const title = mode === "switch" ? "Switch to the retrained model?" : "Keep the model in use?";
  return (
    <form className="form card" aria-label={title} onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <h3 ref={heading} tabIndex={-1}>{title}</h3>
      <ul>
        {mode === "switch" ? (
          <>
            <li>The retrained model becomes the model in use, together with its data, test design and features: they move together.</li>
            <li>Scoring with the model in use uses it from now on; predictions already made do not change.</li>
            <li>You can go back to an earlier approved model on Model → Approved for scoring.</li>
          </>
        ) : (
          <>
            <li>The model in use stays. The retrained run stays in Experiments; nothing is deleted.</li>
            <li>The model in use is not retrained again on this file.</li>
          </>
        )}
      </ul>
      {mode === "switch" && (detail.isError || refs.isError) ? <Banner tone="warn">Could not read what is in use now. Reload and try again, or decide it in the Inbox.</Banner> : null}
      {unusable ? <Banner tone="warn">This switch cannot be made from here. Open the Inbox or History to decide it.</Banner> : null}
      <label className="field"><span>Your reason (saved in History)</span>
        <textarea rows={2} maxLength={RATIONALE_MAX} required value={reason} disabled={write.busy} onChange={(e) => setReason(e.target.value)} />
      </label>
      <ErrorLine error={write.error} />
      <div className="toolbar">
        <button type="button" className="btn" disabled={write.busy} onClick={onClose}>Cancel</button>
        <button type="submit" className={`btn ${mode === "switch" ? "primary" : ""}`} disabled={write.busy || waiting || unusable || !reason.trim()}>
          {write.busy ? "Saving…" : mode === "switch" ? "Yes, switch" : "Yes, keep the model in use"}
        </button>
      </div>
    </form>
  );
}

const SHOWN_STATES = new Set(["ready_to_compare", "waiting_for_you", "switched", "kept"]);

export function ChallengerCard({ projectId, status, canDecide }: { projectId: string; status: StudioRetrainStatus | undefined; canDecide: boolean }) {
  const [mode, setMode] = useState<Mode | null>(null);
  const [done, setDone] = useState<string | null>(null);
  if (!status || (!status.comparison && !SHOWN_STATES.has(status.state))) return null;
  // A retrain of an earlier model in use that nobody switched to is no longer a decision about the model in use.
  if (status.request && status.request.incumbent_model_version_id !== status.model_in_use_id && status.proposal_state !== "accepted") return null;
  const switched = status.state === "switched" || status.proposal_state === "accepted";
  // "Now in use" only while the model in use is no longer the one the retrain challenged.
  const nowInUse = switched && !!status.request && status.request.incumbent_model_version_id !== status.model_in_use_id;
  const view = comparisonView(status.comparison, status.comparison_withheld === true);
  const runId = status.request?.experiment_id;
  const run = isUuid(runId) ? projectHref(projectId, "experiments", runId) : null;
  const proposal = status.state === "waiting_for_you" && status.proposal_state === "proposed" && isUuid(status.proposal_id) ? status.proposal_id : null;
  return (
    <Card title="Retrained model beside the model in use" aside={<Pill tone="det">{WORDING}</Pill>}>
      <section data-testid="challenger-card">
        {view.kind === "none" ? <p role="status">The retrained run finished; DCLab is comparing it with the model in use. Reload in a few minutes.</p> : null}
        {view.kind === "withheld" ? <p>The comparison exists; its figures are shown to people only.</p> : null}
        {view.kind === "not_comparable" ? (
          <>
            <p data-testid="not-comparable"><b>{view.reason}</b></p>
            {view.own ? <p className="muted">{view.own}</p> : null}
          </>
        ) : null}
        {view.kind === "compared" ? (
          <>
            <div className="tbl">
              <table aria-label="Retrained model and model in use, same kind of test, latest months">
                <thead><tr><th scope="col">Measure</th><th scope="col" className="num">{nowInUse ? "Retrained model (now in use)" : "Retrained model"}</th><th scope="col" className="num">{switched ? "Previous model" : "Model in use"}</th></tr></thead>
                <tbody>{view.rows.map((r) => <tr key={r.label}><th scope="row">{r.label}</th><td className="num mono">{r.retrained}</td><td className="num mono">{r.inUse}</td></tr>)}</tbody>
              </table>
            </div>
            <ul>{view.facts.map((f) => <li key={f}>{f}</li>)}</ul>
          </>
        ) : null}
        <h3>Real outcomes of the model in use <Pill tone="warn">{NOT_SAME_DATA}</Pill></h3>
        <p data-testid="real-beside">{realOutcomesText(status.real_outcomes)}</p>
        {run ? <p><Link href={run}>Open the retrained run</Link></p> : null}
        <Done text={done} projectId={projectId} />
        {switched ? <p><Pill tone="ok">Switched</Pill> A person switched to the retrained model{nowInUse ? "; it is now the model in use" : ""}.</p> : null}
        {status.state === "kept" ? <p><Pill tone="gray">Kept</Pill> A person kept the model in use.</p> : null}
        {proposal ? (
          canDecide ? (
            mode ? (
              <Decide key={mode} projectId={projectId} proposalId={proposal} mode={mode} onClose={() => setMode(null)} onDone={(text) => { setMode(null); setDone(text); }} />
            ) : (
              <div className="toolbar">
                <button type="button" className="btn primary" onClick={() => { setMode("switch"); setDone(null); }}>Switch to the retrained model</button>
                <button type="button" className="btn" onClick={() => { setMode("keep"); setDone(null); }}>Keep the model in use</button>
              </div>
            )
          ) : <p className="muted" data-testid="decide-viewer">A person who can run ML work in this workspace decides: switch to the retrained model or keep the model in use.</p>
        ) : null}
      </section>
    </Card>
  );
}

/* ---------- the retrain policy ---------- */

export function RetrainPolicyCard({ projectId, thresholds }: { projectId: string; thresholds: { data?: StudioThresholds; isError: boolean; isPending: boolean } }) {
  const { user } = useSession();
  const read = useRetrainPolicy(projectId);
  const invalidate = useRetrainInvalidation();
  const write = useWrite();
  const [choice, setChoice] = useState<PolicyValue | null>(null);
  const [reason, setReason] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const p = read.data;
  const canEdit = thresholds.data?.can_edit === true; // the same server rule: workspace owners and administrators
  const current = p ? policyWords(p.policy) : null;
  const picked = choice ?? (p && isPolicy(p.policy) ? p.policy : null);
  const changed = !!p && !!picked && picked !== p.policy;
  const save = async () => {
    if (!p || !picked || !changed || !reason.trim()) return;
    const rationale = reason.trim();
    const ok = await write.run("policy", `${picked}:${rationale}:${p.version}`, (key) => setRetrainPolicy({ projectId, policy: picked, rationale, version: p.version, key }), (caught) => mapRetrainError(caught, "policy"));
    invalidate();
    if (ok) { setDone(`Saved: the retrain policy is now ${policyWords(picked).label}.`); setChoice(null); setReason(""); }
  };
  return (
    <Card title="Retrain policy" aside={current ? <Pill tone={p?.policy === "off" ? "gray" : "det"}>{current.label}</Pill> : undefined}>
      <div data-testid="retrain-policy">
        {read.isPending ? <p role="status">Loading the retrain policy…</p> : read.isError || !p ? <RetrainReadFailed what="retrain policy" error={read.error} onRetry={() => void read.refetch()} /> : (
          <>
            <p><b>{current!.label}.</b> {current!.text}</p>
            {policyFacts(p, user?.id ?? null).map((f) => <p key={f} className="muted">{f}</p>)}
            <p className="muted">{RULE_LIMIT} Switching is always a person&apos;s decision.</p>
            <Done text={done} projectId={projectId} />
            {canEdit ? (
              <form className="form" aria-label="Change the retrain policy" onSubmit={(e) => { e.preventDefault(); void save(); }}>
                <fieldset disabled={write.busy}>
                  <legend>When the newest week is an alert and a newer labelled file exists</legend>
                  {POLICIES.map((value) => (
                    <div key={value} className="field">
                      <label><input type="radio" name="retrain-policy" value={value} checked={picked === value} aria-describedby={`policy-${value}-help`} onChange={() => { setChoice(value); setDone(null); }} /> <b>{policyWords(value).label}</b></label>
                      <span id={`policy-${value}-help`} className="muted">{policyWords(value).text}</span>
                    </div>
                  ))}
                </fieldset>
                {picked === "auto" && changed ? <Banner tone="warn">Automatic retrains act as you: they use your permission and count against your plan&apos;s limits. They never switch the model in use.</Banner> : null}
                <label className="field"><span>Your reason (saved in History)</span>
                  <textarea rows={2} maxLength={RATIONALE_MAX} value={reason} disabled={write.busy} onChange={(e) => setReason(e.target.value)} />
                </label>
                <ErrorLine error={write.error} />
                <div className="toolbar">
                  <button type="submit" className="btn primary" disabled={write.busy || !changed || !reason.trim()}>{write.busy ? "Saving…" : changed ? `Save: ${policyWords(picked!).label}` : "Save"}</button>
                </div>
              </form>
            ) : thresholds.isPending ? null : thresholds.isError ? <p className="muted">Whether you can change this could not be checked. Reload the page.</p>
              : <p className="muted" data-testid="policy-read-only">Only workspace owners and administrators can change the retrain policy.</p>}
          </>
        )}
      </div>
    </Card>
  );
}

/* ---------- thresholds ---------- */

const FIELD_LABEL: Record<"psi_watch" | "psi_alert", string> = { psi_watch: "Watch line (PSI)", psi_alert: "Alert line (PSI)" };

function NumberField({ id, label, value, error, disabled, onChange, extra }: { id: string; label: string; value: string; error?: string; disabled: boolean; onChange: (v: string) => void; extra?: ReactNode }) {
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      {extra}
      <input id={id} type="text" inputMode="decimal" value={value} disabled={disabled} aria-invalid={!!error} aria-describedby={error ? `${id}-error` : undefined} onChange={(e) => onChange(e.target.value)} />
      {error ? <p id={`${id}-error`} className="muted" role="alert">{error}</p> : null}
    </div>
  );
}

export function ThresholdsCard({ projectId }: { projectId: string }) {
  const { user } = useSession();
  const read = useMonitoringThresholds(projectId);
  const invalidate = useRetrainInvalidation();
  const write = useWrite();
  const [draft, setDraft] = useState<ThresholdDraft | null>(null);
  const [errors, setErrors] = useState<Partial<Record<DraftField | "form", string>>>({});
  const [review, setReview] = useState<ThresholdValues | null>(null);
  const reviewHeading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { if (review) reviewHeading.current?.focus(); }, [review]);
  const [done, setDone] = useState<string | null>(null);
  const t = read.data;
  const current: ThresholdValues | null = t ? { psi_watch: t.psi_watch, psi_alert: t.psi_alert, precision_floor: t.precision_floor, recall_floor: t.recall_floor } : null;
  const d = draft ?? (current ? draftFrom(current) : null);
  const edit = (patch: Partial<ThresholdDraft>) => { if (d) { setDraft({ ...d, ...patch }); setDone(null); setReview(null); } };
  const check = () => {
    if (!t || !d) return;
    const result = validateThresholds(d, t.bounds, current);
    if (result.ok) { setErrors({}); setReview(result.values); } else setErrors(result.errors);
  };
  const save = async () => {
    if (!t || !d || !review) return;
    const rationale = d.rationale.trim();
    const ok = await write.run("thresholds", `${JSON.stringify(review)}:${rationale}:${t.version}`, (key) => setThresholds({ projectId, values: review, rationale, version: t.version, key }), (caught) => mapRetrainError(caught, "thresholds"));
    invalidate();
    if (ok) { setDone("Saved. Weeks computed with new scored data from now on use the new values; past weeks, and a week recomputed without new scorings, keep the values they used."); setDraft(null); setReview(null); }
  };
  return (
    <Card title="Thresholds" aside={t ? <span className="muted">version {t.version}</span> : undefined}>
      <div data-testid="thresholds">
        {read.isPending ? <p role="status">Loading the thresholds…</p> : read.isError || !t || !current ? <RetrainReadFailed what="thresholds" error={read.error} onRetry={() => void read.refetch()} /> : (
          <>
            <KeyValue items={[
              { key: "watch", label: "Watch line (PSI)", value: <span className="mono">{t.psi_watch}</span> },
              { key: "alert", label: "Alert line (PSI)", value: <span className="mono">{t.psi_alert}</span> },
              { key: "precision", label: "Precision floor (real outcomes)", value: <span className="mono">{t.precision_floor ?? "off"}</span> },
              { key: "recall", label: "Recall floor (real outcomes)", value: <span className="mono">{t.recall_floor ?? "off"}</span> },
            ]} />
            <p data-testid="thresholds-set-by">{setByText(t, user?.id ?? null)}</p>
            <p className="muted">{FLOORS_NOT_DRIFT}</p>
            <Done text={done} projectId={projectId} />
            {t.can_edit && d ? (
              <form className="form" aria-label="Change the thresholds" onSubmit={(e) => { e.preventDefault(); check(); }}>
                <p className="muted">{boundsText(t.bounds)} The defaults are {valuesText(t.defaults)}.</p>
                {(["psi_watch", "psi_alert"] as const).map((k) => (
                  <NumberField key={k} id={`th-${k}`} label={FIELD_LABEL[k]} value={d[k]} error={errors[k]} disabled={write.busy} onChange={(v) => edit({ [k]: v })} />
                ))}
                {(["precision", "recall"] as const).map((name) => {
                  const on = name === "precision" ? d.precision_on : d.recall_on;
                  const key = `${name}_floor` as const;
                  return (
                    <NumberField key={name} id={`th-${key}`} label={`${name === "precision" ? "Precision" : "Recall"} floor`} value={d[key]} error={errors[key]} disabled={write.busy || !on}
                      onChange={(v) => edit({ [key]: v })}
                      extra={<label><input type="checkbox" checked={on} disabled={write.busy} onChange={(e) => edit(name === "precision" ? { precision_on: e.target.checked } : { recall_on: e.target.checked })} /> Use a {name} floor</label>} />
                  );
                })}
                <div className="field">
                  <label htmlFor="th-reason">Your reason (saved in History)</label>
                  <textarea id="th-reason" rows={2} maxLength={RATIONALE_MAX} value={d.rationale} disabled={write.busy} aria-invalid={!!errors.rationale} onChange={(e) => edit({ rationale: e.target.value })} />
                  {errors.rationale ? <p className="muted" role="alert">{errors.rationale}</p> : null}
                </div>
                {errors.form ? <Banner tone="warn">{errors.form}</Banner> : null}
                {review ? (
                  <div className="card flat" role="group" aria-labelledby="th-confirm-title">
                    <h3 id="th-confirm-title" ref={reviewHeading} tabIndex={-1}>Save these thresholds?</h3>
                    <ul>{thresholdChanges(current, review).map((line) => <li key={line}>{line}</li>)}<li>{THRESHOLDS_APPLY}</li><li>The change is saved in History.</li></ul>
                    <ErrorLine error={write.error} />
                    <div className="toolbar">
                      <button type="button" className="btn" disabled={write.busy} onClick={() => setReview(null)}>Back</button>
                      <button type="button" className="btn primary" disabled={write.busy} onClick={() => void save()}>{write.busy ? "Saving…" : "Yes, save thresholds"}</button>
                    </div>
                  </div>
                ) : (
                  <div className="toolbar"><button type="submit" className="btn primary" disabled={write.busy}>Review the change</button></div>
                )}
              </form>
            ) : <p className="muted" data-testid="thresholds-read-only">Only workspace owners and administrators can change the thresholds. {THRESHOLDS_APPLY}</p>}
          </>
        )}
      </div>
    </Card>
  );
}
