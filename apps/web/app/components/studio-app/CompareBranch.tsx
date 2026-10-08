"use client";

/**
 * Branch builder, cancel and "make champion" panels (P4.4-A). Writes carry one Idempotency-Key per user
 * action and are single-flight; a stale ref is an explicit conflict. The API decides every rule; showing or
 * hiding a button here is a convenience only. Names and rationales are untrusted text, rendered as text.
 */
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";
import { QueryNotice } from "@/app/components/studio-app/StudioParts";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";
import { projectHref } from "@/lib/application/command-search";
import { useProjectRefs } from "@/lib/application/studio-hooks";
import { useProjectGraph } from "@/lib/application/studio-data-hooks";
import { cancelExperiment, createBranch, makeChampion, useWriteInvalidation } from "@/lib/application/studio-compare-hooks";
import { useModelVersionRead } from "@/lib/application/studio-inspect-hooks";
import { modelVersionOf } from "@/lib/application/studio-inspect";
import {
  CHANGE_KINDS, TRANSFORMS, branchProblem, buildBranchBody, emptyDraft, mapActionError, type ChangeDraft, type ChangeKind,
} from "@/lib/application/studio-compare";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import type { BranchPrefill } from "@/lib/application/studio-findings";

function Problem({ error }: { error: PlainError }) {
  return <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner>;
}

// --- Branch -------------------------------------------------------------------------------

function ChangeFields({ draft, onChange }: { draft: ChangeDraft; onChange: (patch: Partial<ChangeDraft>) => void }) {
  const text = (label: string, key: keyof ChangeDraft, placeholder?: string) => (
    <label className="field"><span>{label}</span>
      <input value={String(draft[key] as string)} placeholder={placeholder} maxLength={256} onChange={(e) => onChange({ [key]: e.target.value } as Partial<ChangeDraft>)} />
    </label>
  );
  const area = (label: string, key: "params" | "weights", placeholder: string) => (
    <label className="field"><span>{label}</span>
      <textarea rows={3} value={draft[key]} placeholder={placeholder} maxLength={2000} onChange={(e) => onChange({ [key]: e.target.value } as Partial<ChangeDraft>)} />
    </label>
  );
  switch (draft.kind) {
    case "hyperparameter_override":
      return <>{text("Model family", "family", "for example random_forest")}{area("Parameters (one name=value per line)", "params", "max_depth=4")}</>;
    case "family_include":
    case "family_exclude":
      return text("Model family", "family", "for example logistic_regression");
    case "class_weighting":
      return (
        <>
          <label className="field"><span>Mode</span>
            <select value={draft.mode} onChange={(e) => onChange({ mode: e.target.value as ChangeDraft["mode"] })}>
              <option value="none">none</option><option value="balanced">balanced</option><option value="custom">custom</option>
            </select>
          </label>
          {draft.mode === "custom" ? area("Weights (one class=weight per line)", "weights", "yes=3") : null}
        </>
      );
    case "threshold_objective":
      return (
        <>
          <div className="toolbar">
            <label className="field"><span>Constraint metric</span><input value={draft.constraint.metric} placeholder="recall" onChange={(e) => onChange({ constraint: { ...draft.constraint, metric: e.target.value } })} /></label>
            <label className="field"><span>Operator</span>
              <select value={draft.constraint.op} onChange={(e) => onChange({ constraint: { ...draft.constraint, op: e.target.value as ">=" | "<=" } })}><option value=">=">at least (&gt;=)</option><option value="<=">at most (&lt;=)</option></select>
            </label>
            <label className="field"><span>Value</span><input inputMode="decimal" value={draft.constraint.value} onChange={(e) => onChange({ constraint: { ...draft.constraint, value: e.target.value } })} /></label>
          </div>
          <div className="toolbar">
            <label className="field"><span>Cost of a false positive</span><input inputMode="decimal" value={draft.costFp} onChange={(e) => onChange({ costFp: e.target.value })} /></label>
            <label className="field"><span>Cost of a false negative</span><input inputMode="decimal" value={draft.costFn} onChange={(e) => onChange({ costFn: e.target.value })} /></label>
          </div>
        </>
      );
    case "metric_override":
      return <>{text("Primary metric", "metric", "for example pr_auc")}{text("Reason", "reason")}</>;
    case "feature_transform_add":
    case "feature_transform_remove":
      return (
        <>
          {text("Column", "column")}
          <label className="field"><span>Transform</span>
            <select value={draft.transform} onChange={(e) => onChange({ transform: e.target.value as ChangeDraft["transform"] })}>
              {TRANSFORMS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </label>
        </>
      );
  }
}

export function BranchPanel({ projectId, experimentId, initial }: { projectId: string; experimentId: string; initial?: BranchPrefill | null }) {
  const router = useRouter();
  const invalidate = useWriteInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const nextId = useRef(1);
  const [intent, setIntent] = useState(initial?.intent ?? "");
  const [drafts, setDrafts] = useState<ChangeDraft[]>(() => [initial?.draft ?? emptyDraft(0)]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const body = buildBranchBody(intent, drafts);
  const preview = typeof body === "string" ? null : JSON.stringify(body, null, 2);
  const patch = (id: number, change: Partial<ChangeDraft>) => setDrafts((all) => all.map((d) => (d.id === id ? { ...d, ...change } : d)));
  const flight = useRef(false);
  const send = async () => {
    if (flight.current || typeof body === "string") return;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      const created = await createBranch({ parentId: experimentId, body, key: keys.keyFor("branch", `${experimentId}:${JSON.stringify(body)}`) });
      keys.done("branch");
      invalidate();
      const href = projectHref(projectId, "experiments", created.id);
      if (href) router.push(href);
    } catch (caught) {
      setError(branchProblem(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <Card title="Try a change" aside={<Pill tone="det">one change on top</Pill>}>
      {initial ? <Banner tone="info">Pre-filled from a finding. Review the change, add your reason if you want to change it, then start the branch; nothing runs until you do.</Banner> : null}
      <p className="muted">
        Re-run this run with <Term definition="A list of changes applied on top of this run. The test design and data stay the same, so the new run can be compared with this one.">changes</Term> on top.
        Pick the changes below; the API validates them when you start the branch and its answer is shown here unchanged.
      </p>
      <form className="form" onSubmit={(event) => { event.preventDefault(); void send(); }}>
        <label className="field"><span>Why are you branching? (recorded with the run)</span>
          <textarea rows={2} value={intent} maxLength={2000} onChange={(e) => setIntent(e.target.value)} disabled={busy} />
        </label>
        {drafts.map((draft, index) => (
          <fieldset key={draft.id} className="card flat" disabled={busy}>
            <legend>Change {index + 1}</legend>
            <label className="field"><span>Kind of change</span>
              <select value={draft.kind} onChange={(e) => patch(draft.id, { ...emptyDraft(draft.id, e.target.value as ChangeKind) })}>
                {CHANGE_KINDS.map((k) => <option key={k.kind} value={k.kind}>{k.label}</option>)}
              </select>
            </label>
            <p className="muted">{CHANGE_KINDS.find((k) => k.kind === draft.kind)?.help}</p>
            <ChangeFields draft={draft} onChange={(change) => patch(draft.id, change)} />
            {drafts.length > 1 ? <button type="button" className="btn" onClick={() => setDrafts((all) => all.filter((d) => d.id !== draft.id))}>Remove change {index + 1}</button> : null}
          </fieldset>
        ))}
        <div className="toolbar">
          <button type="button" className="btn" disabled={busy || drafts.length >= 32} onClick={() => setDrafts((all) => [...all, emptyDraft(nextId.current++)])}>Add another change</button>
        </div>
        <h3>What will be sent</h3>
        {typeof body === "string" ? <p className="muted" role="status">{body}</p> : (
          <pre className="code" aria-label="Change set that will be sent">{preview}</pre>
        )}
        <p className="muted">There is no client-side rule check: the API refuses an unknown family, a bad class, a leakage column or a duplicate change with its own message, and nothing is started.</p>
        {error ? <Problem error={error} /> : null}
        <div className="toolbar">
          <button type="submit" className="btn primary" disabled={busy || typeof body === "string"}>{busy ? "Starting…" : "Start branch"}</button>
        </div>
      </form>
    </Card>
  );
}

// --- Cancel -------------------------------------------------------------------------------

export function CancelPanel({ experimentId }: { experimentId: string }) {
  const invalidate = useWriteInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const flight = useRef(false);
  const send = async () => {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await cancelExperiment({ experimentId, key: keys.keyFor("cancel", experimentId) });
      keys.done("cancel");
      setDone(result.status === "cancelling" ? "The worker will stop at its next checkpoint." : "The run was cancelled.");
      setConfirming(false);
      invalidate();
    } catch (caught) {
      setError(mapActionError(caught, "cancel"));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <div className="toolbar" role="group" aria-label="Cancel this run">
      {done ? <Pill tone="gray">{done}</Pill> : null}
      {!confirming ? <button type="button" className="btn" onClick={() => setConfirming(true)}>Cancel run</button> : (
        <>
          <span>Stop this run? Work already done is kept as evidence.</span>
          <button type="button" className="btn" disabled={busy} onClick={() => setConfirming(false)}>Keep running</button>
          <button type="button" className="btn primary" disabled={busy} onClick={() => void send()}>{busy ? "Cancelling…" : "Yes, cancel this run"}</button>
        </>
      )}
      {error ? <Problem error={error} /> : null}
    </div>
  );
}

// --- Make champion ------------------------------------------------------------------------

export function ChampionPanel({ projectId, experimentId, compact }: { projectId: string; experimentId: string; compact?: boolean }) {
  const graph = useProjectGraph(projectId);
  const refs = useProjectRefs(projectId);
  const modelVersionId = graph.data ? modelVersionOf(graph.data.edges, experimentId) : null;
  const model = useModelVersionRead(modelVersionId);
  const invalidate = useWriteInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const [open, setOpen] = useState(false);
  const [rationale, setRationale] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const [moved, setMoved] = useState(false);
  const flight = useRef(false);
  const decisions = projectHref(projectId, "decisions");
  if (graph.isError) return <QueryNotice error={graph.error} what="project graph" />;
  if (refs.isError) return <QueryNotice error={refs.error} what="versions in use" />;
  if (graph.isPending || refs.isPending) return <p role="status">Checking the model in use…</p>;
  if (!modelVersionId) return <p className="muted">No model yet: a run gets one when its best run is chosen, and only then can it be put in use.</p>;
  if (model.isError) return <QueryNotice error={model.error} what="model version" />;
  if (!model.data) return <p role="status">Loading the model version…</p>;
  const m = model.data;
  const champion = refs.data?.items.find((r) => r.ref_kind === "champion_model");
  const recipe = refs.data?.items.find((r) => r.ref_kind === "feature_recipe");
  const isChampion = m.is_champion || champion?.target.id === m.id;
  const confirm = async () => {
    if (flight.current || !rationale.trim()) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    try {
      await makeChampion({
        projectId, modelVersionId: m.id, candidateId: m.lineage.candidate_id, featureRecipeId: m.lineage.feature_recipe_id ?? null,
        champion, recipe, rationale: rationale.trim(), key: keys.keyFor("champion", `${m.id}:${rationale.trim()}`),
      });
      keys.done("champion");
      setMoved(true);
      setOpen(false);
      setRationale("");
      invalidate();
    } catch (caught) {
      setError(mapActionError(caught, "champion"));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <section aria-label="Is this model in use?">
      <p>
        This run&apos;s model {isChampion ? <Pill tone="ok">★ in use</Pill> : <Pill tone="gray">not in use</Pill>}
        {" "}{champion && !isChampion ? <span className="muted">Another model is in use now.</span> : !champion ? <span className="muted">No model is in use yet.</span> : null}
      </p>
      {moved ? <Banner tone="info">This model is now in use and the change was saved in History.{decisions ? <> <Link href={decisions}>Open History</Link>.</> : null}</Banner> : null}
      {!isChampion && !open ? <button type="button" className="btn primary" onClick={() => { setOpen(true); setMoved(false); setError(null); }}>Put this model in use</button> : null}
      {open ? (
        <form className="form card" onSubmit={(event) => { event.preventDefault(); void confirm(); }}>
          <h3>Put this model in use</h3>
          {compact ? null : (
            <p className="muted">
              This is saved in History and switches the model in use (and its features with it). DCLab checks that this model has a chosen best run, its
              own final test and the <Term definition="The fixed assignment of rows to folds and the final test set (used once). A new model in use must be tested on the same test design as the current one.">same test design</Term> as the model in use now. Nothing is retrained.
            </p>
          )}
          <label className="field"><span>Why (saved with the change)</span>
            <textarea value={rationale} rows={2} maxLength={2000} required disabled={busy} onChange={(e) => setRationale(e.target.value)} />
          </label>
          {error ? (
            <Banner tone="crit" actions={<button type="button" className="btn" onClick={() => { invalidate(); setError(null); }}>Reload</button>}><b>{error.title}.</b> {error.detail}</Banner>
          ) : null}
          <div className="toolbar">
            <button type="button" className="btn" disabled={busy} onClick={() => setOpen(false)}>Cancel</button>
            <button type="submit" className="btn primary" disabled={busy || !rationale.trim()}>{busy ? "Saving…" : "Put in use"}</button>
          </div>
        </form>
      ) : null}
      {!open && error ? <Problem error={error} /> : null}
    </section>
  );
}
