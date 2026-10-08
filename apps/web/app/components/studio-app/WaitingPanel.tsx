"use client";

import { useMemo, useRef, useState } from "react";
import { AgentText } from "@/components/studio/AgentText";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { Pill } from "@/components/studio/Pill";
import { Term } from "@/components/studio/Term";
import { ActionKeys, confirmSplit, confirmTarget, mapWizardError, useExecutionRequest, useWizardInvalidation, type PlainError } from "@/lib/application";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";
import { QueryNotice } from "./StudioParts";

type Candidate = { name: string; confidence: number };
const text = (value: unknown): string => (typeof value === "string" ? value : "");

/** A run parked in needs_input: the rule's question and any AI suggestion, side by side, with the confirm action. */
export function WaitingPanel({ requestId, projectId, experimentId }: { requestId: string | null | undefined; projectId: string; experimentId: string }) {
  const waiting = useExecutionRequest(requestId, true);
  const invalidate = useWizardInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const [chosen, setChosen] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const summary = waiting.data?.result_summary ?? {};
  const candidates = useMemo<Candidate[]>(
    () => (Array.isArray(summary.possible_columns) ? summary.possible_columns : [])
      .filter((c): c is { name: string; confidence?: number } => typeof c?.name === "string")
      .map((c) => ({ name: c.name, confidence: Number(c.confidence) || 0 })),
    [summary.possible_columns],
  );
  if (!requestId) return <Banner tone="warn">This run is waiting for your answer, but its request could not be found.</Banner>;
  if (waiting.isPending) return <p role="status">Loading the question…</p>;
  if (waiting.isError) return <QueryNotice error={waiting.error} what="question" />;
  if (waiting.data.status !== "needs_input") return null;

  const isSplit = summary.kind === "split_strategy_confirmation";
  const rule = text(summary.recommended_column);
  const ai = summary.ai_suggestion && typeof summary.ai_suggestion === "object" ? (summary.ai_suggestion as Record<string, unknown>) : null;
  const aiTarget = text(ai?.target_column);
  const answer = async (send: () => Promise<unknown>, action: string) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await send();
      keys.done(action);
      invalidate(projectId, experimentId);
      void waiting.refetch();
    } catch (caught) {
      setError(mapWizardError(caught));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title="DCLab needs your answer to continue" aside={<Pill tone="warn">needs input</Pill>}>
      {error ? <Banner tone="crit"><b>{error.title}.</b> {error.detail}</Banner> : null}
      {isSplit ? (
        <>
          <AgentText text={text(summary.reason) || "The run plan changes how the data is split."} />
          <p>Nothing is split until you answer. Keeping the rule&apos;s split uses the fixed, deterministic plan.</p>
          <button type="button" className="btn primary" disabled={busy}
            onClick={() => void answer(() => confirmSplit({ requestId, key: keys.keyFor("split", requestId) }), "split")}>
            {busy ? "Sending…" : "Keep the rule's split and continue"}
          </button>
        </>
      ) : (
        <>
          <p>{text(summary.reason) || "Several columns could be the target."} Pick the <Term definition="The column you want to predict.">target</Term> to continue.</p>
          <div className="grid cols-2">
            <div><h3>Rule suggestion</h3><p><Pill tone="det">rule</Pill> {rule ? <span className="mono">{rule}</span> : "none"}</p></div>
            <div><h3>AI suggestion</h3><p>{aiTarget ? <><Pill tone="ai">AI, advisory</Pill> <span className="mono">{aiTarget}</span></> : "None. AI is off or had no answer; the rule decides."}</p></div>
          </div>
          <label className="field"><span>Target column</span>
            <select value={chosen} onChange={(e) => setChosen(e.target.value)} disabled={busy}>
              <option value="">Choose a column</option>
              {candidates.map((c) => <option key={c.name} value={c.name}>{c.name} (score {c.confidence.toFixed(2)})</option>)}
            </select>
          </label>
          <button type="button" className="btn primary" disabled={busy || !chosen}
            onClick={() => void answer(() => confirmTarget({ requestId, targetColumn: chosen, key: keys.keyFor("target", `${requestId}:${chosen}`) }), "target")}>
            {busy ? "Sending…" : "Confirm and resume"}
          </button>
        </>
      )}
    </Card>
  );
}
