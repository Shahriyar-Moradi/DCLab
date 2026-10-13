"use client";

/**
 * "Weekly scoring" (V7-B2b-UI): the project's weekly schedule on the Predictions screen. Each week DCLab scores the newest
 * file uploaded for scoring with the model in use if a person approved it; it never retrains, fetches data or switches models.
 * Saving is a signed-in person's act with one confirm step. Every value comes from the schedule read; untrusted text is plain text.
 */
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { Pill } from "@/components/studio/Pill";
import { isUuid, projectHref } from "@/lib/application/command-search";
import { useSession } from "@/lib/application/session-provider";
import { useProjectRefs } from "@/lib/application/studio-hooks";
import { modelLabel } from "@/lib/application/studio-releases";
import { useProjectReleases } from "@/lib/application/studio-releases-hooks";
import {
  ABOUT_LINES, HOURS, REASON_MAX, WEEKDAYS, confirmWords, isConflict, localHint, mapScheduleError, modelUsed, nextRunText, ownerWarning, reasonToSend, runRows, savedText,
  scheduleAction, scheduleView, slotText, type Intent, type ScheduleLike,
} from "@/lib/application/studio-schedule";
import { saveScoringSchedule, useScheduleWrites, useScoringSchedule } from "@/lib/application/studio-schedule-hooks";
import { usePredictionsById } from "@/lib/application/studio-scoring-hooks";
import { ActionKeys, type PlainError } from "@/lib/application/studio-wizard";
import { CAPABILITIES, hasCapability } from "@/lib/infrastructure/capabilities";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

const STATUS_ID = "weekly-scoring-status";
const localTime = (d: Date) => d.toLocaleString(undefined, { weekday: "long", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });

function ConfirmSave({ projectId, intent, model, paused, current, onClose, onDone }: {
  projectId: string; intent: Intent; model: ReturnType<typeof modelUsed>; paused: boolean; current: ScheduleLike | null; onClose: () => void; onDone: (text: string) => void;
}) {
  const { saved, invalidate } = useScheduleWrites(projectId);
  // The same request resent keeps its key; a changed day, hour, switch or reason is a new action with a new key.
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<PlainError | null>(null);
  const words = confirmWords(intent, model, { paused, current });
  useEffect(() => heading.current?.focus(), []);
  const send = async () => {
    if (flight.current) return;
    flight.current = true;
    setBusy(true);
    setError(null);
    const text = reasonToSend(reason);
    const fingerprint = `${intent.enabled}:${intent.weekday}:${intent.hour}:${text ?? ""}`;
    try {
      const result = await saveScoringSchedule({ projectId, weekday: intent.weekday, hourUtc: intent.hour, enabled: intent.enabled, rationale: text, key: keys.keyFor("save", fingerprint) });
      keys.done("save");
      saved(result); // what the save returned is shown at once; the re-read follows
      invalidate();
      onDone(savedText(intent, result.status));
    } catch (caught) {
      if (isConflict(caught)) keys.done("save"); // the same request can never succeed: the next try is new
      setError(mapScheduleError(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <form className="form card" aria-label={words.title} onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <h3 ref={heading} tabIndex={-1}>{words.title}</h3>
      <ul>{words.lines.map((line) => <li key={line}>{line}</li>)}</ul>
      <label className="field"><span>Why (optional, saved with the change)</span>
        <textarea value={reason} rows={2} maxLength={REASON_MAX} disabled={busy} onChange={(e) => setReason(e.target.value)} />
      </label>
      {error ? (
        <Banner tone="crit" actions={<button type="button" className="btn" onClick={() => { invalidate(); setError(null); }}>Reload</button>}><b>{error.title}.</b> {error.detail}</Banner>
      ) : null}
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="submit" className="btn primary" disabled={busy}>{busy ? "Saving…" : words.button}</button>
      </div>
    </form>
  );
}

export function WeeklyScoring({ projectId }: { projectId: string }) {
  const { user } = useSession();
  const canWrite = hasCapability(user, CAPABILITIES.workspaceExecuteMl);
  const read = useScoringSchedule(projectId);
  const refs = useProjectRefs(projectId);
  const releases = useProjectReleases(projectId);
  const [draft, setDraft] = useState<{ weekday: number; hour: number } | null>(null);
  const [intent, setIntent] = useState<Intent | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [focusTo, setFocusTo] = useState<string | null>(null);
  const state = read.data;
  const schedule = state?.schedule ?? null;
  const weekday = draft?.weekday ?? schedule?.weekday ?? 0;
  const hour = draft?.hour ?? schedule?.hour_utc ?? 9;
  const championId = refs.data?.items.find((r) => r.ref_kind === "champion_model")?.target.id ?? null;
  const current = releases.data?.items?.find((r) => r.is_current) ?? null;
  // Nothing is claimed about the model unless both reads succeeded.
  const checked = !!refs.data && !!releases.data && !refs.isError && !releases.isError;
  const checkFailed = refs.isError || releases.isError;
  const model = modelUsed({ inUseId: isUuid(championId) ? championId : null, inUseName: current ? modelLabel(current.model_version) : null, approvedId: current?.model_version_id ?? null, checked });
  // A failed refresh with data already shown keeps the card (and an open confirm step); only "no data at all" is the error state.
  const view = scheduleView({ pending: read.isPending, failed: read.isError && !read.data, state, model: checked ? model : null });
  const models = projectHref(projectId, "models");
  const rawRuns = (state?.recent_runs ?? []).slice(0, 10);
  const predictionIds = rawRuns.flatMap((r) => (r.outcome === "queued" && isUuid(r.batch_prediction_id) ? [r.batch_prediction_id as string] : []));
  const predictionReads = usePredictionsById(predictionIds);
  const predictions = new Map(predictionIds.flatMap((id, i) => (predictionReads[i]?.data ? [[id, predictionReads[i].data!] as const] : [])));
  const runs = runRows(rawRuns, isUuid, predictions);
  const now = new Date();
  const next = nextRunText(view, schedule, now);
  const hint = schedule && schedule.enabled && view.kind !== "paused" ? localHint(schedule.weekday, schedule.hour_utc, now, localTime) : null;
  const warning = ownerWarning(schedule);
  const editable = scheduleAction({ canWrite, confirming: !!intent });
  const ready = view.kind !== "error" && view.kind !== "loading";
  const idle = view.kind === "on_idle";

  useEffect(() => {
    if (!focusTo) return;
    document.getElementById(focusTo)?.focus();
    setFocusTo(null);
  }, [focusTo]);

  const open = (next: Intent) => { setIntent(next); setDone(null); };
  const on = view.kind === "on" || view.kind === "on_idle" || view.kind === "paused";
  return (
    <Card title="Weekly scoring" aside={view.kind === "on" ? <Pill tone="ok">On</Pill> : idle ? <Pill tone="warn">On, will score nothing</Pill> : view.kind === "paused" ? <Pill tone="warn">Paused by the platform</Pill> : view.kind === "off" || view.kind === "not_set_up" ? <Pill tone="gray">Off</Pill> : undefined}>
      {view.kind === "error" ? (
        <Banner tone="crit" actions={<button type="button" className="btn" onClick={() => void read.refetch()}>Reload</button>}><b>The weekly schedule could not be loaded.</b> Nothing was changed. Check your connection and reload.</Banner>
      ) : view.kind === "loading" ? <p role="status">Loading the weekly schedule…</p>
        : <p id={STATUS_ID} tabIndex={-1} data-testid="weekly-status"><b>{view.sentence}</b></p>}
      {read.isError && read.data ? (
        <Banner tone="warn" actions={<button type="button" className="btn" onClick={() => void read.refetch()}>Reload</button>}>The weekly schedule could not be refreshed, so what is shown may be out of date.</Banner>
      ) : null}
      <div role="status" aria-live="polite" className={done ? "banner" : undefined}>
        {done ? <>{done}{projectHref(projectId, "decisions") ? <> <Link href={projectHref(projectId, "decisions") as string}>Open History</Link>.</> : null}</> : null}
      </div>
      {schedule && ready ? (
        <>
          <p data-testid="weekly-slot">{slotText(schedule.weekday, schedule.hour_utc)}{hint ? <span className="muted"> · {hint}</span> : null}</p>
          {next ? <p data-testid="weekly-next">{next}</p> : null}
        </>
      ) : null}
      {warning && view.kind !== "on_idle" ? <Banner tone="warn">{warning}</Banner> : null}
      {ready && (checked || checkFailed) ? (
        model.kind === "approved" ? <p data-testid="weekly-model">Model it would use: {model.text}.</p> : (
          <Banner tone="warn">
            <span data-testid="weekly-model">{model.text}</span>{" "}
            {model.kind === "warning" && models ? <Link href={`${models}?tab=approved`}>Open Approved for scoring</Link> : null}
          </Banner>
        )
      ) : null}
      <div className="muted" data-testid="weekly-about">{ABOUT_LINES.map((line) => <p key={line}>{line}</p>)}</div>
      {ready ? (
        canWrite ? (
          <>
            {intent ? (
              <ConfirmSave
                key={`${intent.enabled}:${intent.weekday}:${intent.hour}`} projectId={projectId} intent={intent} model={model} paused={view.kind === "paused"} current={schedule}
                onClose={() => { setFocusTo("weekly-open"); setIntent(null); }}
                onDone={(text) => { setDone(text); setIntent(null); setDraft(null); setFocusTo(STATUS_ID); }}
              />
            ) : null}
            <div className="form" role="group" aria-label="Weekly scoring settings">
              <label className="field"><span>Day of the week</span>
                <select value={weekday} disabled={!editable} onChange={(e) => setDraft({ weekday: Number(e.target.value), hour })}>
                  {WEEKDAYS.map((name, i) => <option key={name} value={i}>{name}</option>)}
                </select>
              </label>
              <label className="field"><span>Hour (UTC)</span>
                <select value={hour} disabled={!editable} onChange={(e) => setDraft({ weekday, hour: Number(e.target.value) })}>
                  {HOURS.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00 UTC</option>)}
                </select>
              </label>
              <p className="muted">{slotText(weekday, hour)}. {localHint(weekday, hour, now, localTime)}</p>
              {editable ? (
                <div className="toolbar">
                  <button id="weekly-open" type="button" className="btn primary" onClick={() => open({ enabled: true, weekday, hour })}>{on ? "Save change" : "Turn on weekly scoring"}</button>
                  {on && schedule ? <button type="button" className="btn" onClick={() => open({ enabled: false, weekday: schedule.weekday, hour: schedule.hour_utc })}>Turn off weekly scoring</button> : null}
                </div>
              ) : null}
            </div>
          </>
        ) : <p className="muted">Only people who can change this workspace can turn weekly scoring on, change it or turn it off.</p>
      ) : null}
      {ready ? (
        <>
          <h3>Last weeks</h3>
          {runs.length ? (
            <ul className="approvals" aria-label="Last weekly runs, newest first">
              {runs.map((r) => {
                const href = r.predictionId ? `/projects/${projectId}/predictions#prediction-${r.predictionId}` : null;
                return (
                  <li key={r.id}>
                    <p><b>{r.week}</b>{r.failed ? <> <Pill tone="crit">failed</Pill></> : null}<br />
                      <span>{r.sentence}</span>{href && projectHref(projectId, "predictions") ? <> <Link href={href}>See the weekly list</Link></> : null}
                    </p>
                  </li>
                );
              })}
            </ul>
          ) : <p className="muted">No week has been handled yet.</p>}
        </>
      ) : null}
    </Card>
  );
}
