"use client";

/**
 * D8b: Settings → Integrations → Webhooks. The workspace's signed webhooks (owners and administrators; the API answers 403
 * `webhooks_not_permitted` to everyone else, and that is shown as such). Add, make a new secret, send a test call, turn on / off
 * and read the delivery log. A signing secret from a create / rotate answer lives only in this component's state: it is shown
 * once with a copy button and cleared when the person hides it, leaves the tab or reloads. Names and addresses are customer text
 * shown as plain text, never as links.
 */
import { useEffect, useRef, useState } from "react";
import { CopyButton } from "@/app/components/studio-app/CopyButton";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { DataTable, type Column } from "@/components/studio/DataTable";
import { Pill } from "@/components/studio/Pill";
import {
  SECRET_ONCE, TEST_QUEUED, WEBHOOK_EVENTS, WEBHOOK_SAFETY, WEBHOOKS_MAX, deliveryRows, shownSecret, validateWebhook, webhookError, webhookRows, webhooksReadError,
  type DeliveryRow, type WebhookAction, type WebhookRow,
} from "@/lib/application/studio-notifications";
import {
  createWebhook, rotateWebhookSecret, setWebhookEnabled, testWebhook, useWebhookDeliveries, useWebhookInvalidation, useWebhooks,
} from "@/lib/application/studio-notifications-hooks";
import { isUuid } from "@/lib/application/command-search";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

type Secret = { webhookName: string; value: string } | null;
/** What a create / rotate answer carried: a readable secret, none (an Idempotency-Key replay), or something unreadable. */
type SecretAnswer = { webhookName: string; secret: unknown };

/** The one-time secret. Cleared on "hide" and on unmount; never written to storage or the query cache. */
function SecretOnce({ secret, onHide }: { secret: NonNullable<Secret>; onHide: () => void }) {
  return (
    <Banner tone="warn" actions={<button type="button" className="btn" onClick={onHide}>I saved it, hide the secret</button>}>
      <p><b>Signing secret for {secret.webhookName}</b></p>
      <p>{SECRET_ONCE}</p>
      <p className="toolbar"><code className="mono" data-testid="webhook-secret" style={{ overflowWrap: "anywhere" }}>{secret.value}</code> <CopyButton text={secret.value} label={`Copy the signing secret of ${secret.webhookName}`} /></p>
    </Banner>
  );
}

function Deliveries({ workspaceId, webhookId, name }: { workspaceId: string; webhookId: string; name: string }) {
  const read = useWebhookDeliveries(workspaceId, webhookId);
  if (read.isPending) return <p role="status">Loading the delivery log…</p>;
  if (read.isError) return <Banner tone="crit">{webhooksReadError(read.error, "delivery log").text}</Banner>;
  const columns: Column<DeliveryRow>[] = [
    { key: "when", header: "When (UTC)" },
    { key: "event", header: "Event" },
    { key: "attempt", header: "Try", numeric: true, render: (r) => String(r.attempt) },
    { key: "outcome", header: "Result", render: (r) => <Pill tone={r.outcome === "Delivered" ? "ok" : "crit"}>{r.outcome}</Pill> },
    { key: "status", header: "HTTP status", numeric: true },
    { key: "reason", header: "Why it failed" },
    { key: "duration", header: "Time", numeric: true },
  ];
  return (
    <>
      <DataTable caption={`Delivery log of ${name}, newest first`} columns={columns} rows={deliveryRows(read.data)} rowKey={(r) => r.id} emptyMessage="No call has been made to this address yet." scrollLabel={`Delivery log of ${name} (scrolls sideways on small screens)`} />
      <p className="small muted">The newest 50 tries. The answer&apos;s body is never stored, only its status and a closed reason.</p>
    </>
  );
}

function AddForm({ workspaceId, full, onSecret }: { workspaceId: string; full: boolean; onSecret: (s: SecretAnswer) => void }) {
  const invalidate = useWebhookInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["decision_needed", "drift_alert"]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  if (full) return <p className="muted" role="note">This workspace has {WEBHOOKS_MAX} webhooks, the most allowed, so no more can be added.</p>;
  const send = async () => {
    if (flight.current) return;
    const checked = validateWebhook({ name, url, events });
    if (!checked.ok) { setProblem(checked.problem); return; }
    flight.current = true;
    setBusy(true); setProblem(null);
    try {
      const created = await createWebhook(workspaceId, checked.body, keys.keyFor("add", JSON.stringify(checked.body)));
      keys.done("add");
      setName(""); setUrl("");
      onSecret({ webhookName: checked.body.name, secret: created.secret });
      invalidate();
    } catch (caught) {
      setProblem(webhookError(caught, "adding the webhook"));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <form className="form" aria-label="Add a webhook" onSubmit={(e) => { e.preventDefault(); void send(); }}>
      <label className="field"><span>Name</span><input value={name} maxLength={80} disabled={busy} onChange={(e) => setName(e.target.value)} placeholder="Our alerts channel" /></label>
      <label className="field"><span>Address (https)</span><input value={url} maxLength={2048} disabled={busy} inputMode="url" onChange={(e) => setUrl(e.target.value)} placeholder="https://hooks.example.com/dclab" /></label>
      <fieldset className="field" disabled={busy}>
        <legend>Events to send</legend>
        {WEBHOOK_EVENTS.map((ev) => (
          <label key={ev.id} className="toolbar">
            <input type="checkbox" checked={events.includes(ev.id)} onChange={(e) => setEvents((all) => (e.target.checked ? [...all, ev.id] : all.filter((x) => x !== ev.id)))} /> {ev.label}
          </label>
        ))}
      </fieldset>
      {problem ? <Banner tone="crit">{problem}</Banner> : null}
      <div className="toolbar"><button type="submit" className="btn primary" disabled={busy}>{busy ? "Adding…" : "Add webhook"}</button></div>
    </form>
  );
}

function WebhookCard({ workspaceId, row, onSecret }: { workspaceId: string; row: WebhookRow; onSecret: (s: SecretAnswer) => void }) {
  const invalidate = useWebhookInvalidation();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [busy, setBusy] = useState<WebhookAction | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [showLog, setShowLog] = useState(false);
  const act = async (action: WebhookAction, work: (key: string) => Promise<void>) => {
    if (flight.current || !isUuid(row.id)) return;
    flight.current = true;
    setBusy(action); setProblem(null); setNotice(null);
    try {
      // The fingerprint carries what is sent (on / off), so a retry after a refetch never reuses a key for the other body.
      await work(keys.keyFor(action, `${row.id}:${action === "turning the webhook on or off" ? String(!row.enabled) : ""}`));
      keys.done(action);
      invalidate();
    } catch (caught) {
      setProblem(webhookError(caught, action));
    } finally {
      flight.current = false;
      setBusy(null);
    }
  };
  const rotate = () => act("making a new secret", async (key) => {
    const answer = await rotateWebhookSecret(workspaceId, row.id, key);
    setConfirmRotate(false);
    onSecret({ webhookName: row.name, secret: answer.secret });
  });
  const ping = () => act("sending a test call", async (key) => { await testWebhook(workspaceId, row.id, key); setNotice(TEST_QUEUED); });
  const toggle = () => act("turning the webhook on or off", async (key) => { await setWebhookEnabled(workspaceId, row.id, !row.enabled, key); });
  return (
    <article className="card flat" aria-label={`Webhook ${row.name}`} data-webhook={row.id}>
      <h3 className="toolbar"><span data-untrusted="">{row.name}</span> <Pill tone={row.state.tone}>{row.state.label}</Pill></h3>
      <p className="mono small" data-untrusted="" style={{ overflowWrap: "anywhere" }}>{row.url}</p>
      <p className="small"><b>Events:</b> {row.events}</p>
      <p className="small"><b>Last call:</b> {row.last}</p>
      <p className="small muted">Signing secret: {row.secret}. Added {row.created}.</p>
      {problem ? <Banner tone="crit">{problem}</Banner> : null}
      {notice ? <p role="status">{notice}</p> : null}
      {confirmRotate ? (
        <Banner tone="warn" actions={(
          <>
            <button type="button" className="btn" disabled={busy !== null} onClick={() => setConfirmRotate(false)}>Cancel</button>
            <button type="button" className="btn primary" disabled={busy !== null} onClick={() => void rotate()}>{busy === "making a new secret" ? "Making…" : "Yes, make a new secret"}</button>
          </>
        )}>
          The current secret stops working at once. Calls are signed with the new one from now on, so update your receiving code right after.
        </Banner>
      ) : null}
      <div className="toolbar">
        <button type="button" className="btn" disabled={busy !== null || !row.enabled} onClick={() => void ping()}>{busy === "sending a test call" ? "Queuing…" : "Send a test call"}</button>
        <button type="button" className="btn" disabled={busy !== null || confirmRotate} onClick={() => setConfirmRotate(true)}>Make a new secret</button>
        <button type="button" className="btn" disabled={busy !== null} onClick={() => void toggle()}>{busy === "turning the webhook on or off" ? "Saving…" : row.enabled ? "Turn off" : "Turn on"}</button>
        <button type="button" className="btn" aria-expanded={showLog} onClick={() => setShowLog((v) => !v)}>{showLog ? "Hide the delivery log" : "Show the delivery log"}</button>
      </div>
      {!row.enabled ? <p className="small muted">A test call needs the webhook turned on. Webhooks are turned off, never deleted, so their delivery log stays.</p> : null}
      {showLog ? <Deliveries workspaceId={workspaceId} webhookId={row.id} name={row.name} /> : null}
    </article>
  );
}

/** `canManage`: the session says the caller manages members (owners and administrators); the server decides either way. */
export function WebhooksPanel({ workspaceId, canManage, ready }: { workspaceId: string | null; canManage: boolean; ready: boolean }) {
  const read = useWebhooks(workspaceId, ready && canManage);
  const [secret, setSecret] = useState<Secret>(null);
  const [noSecret, setNoSecret] = useState<"replay" | "unreadable" | null>(null);
  // Leaving the tab unmounts this panel: the secret goes with it.
  useEffect(() => () => setSecret(null), []);
  if (!ready) return <p role="status">Loading your access…</p>;
  if (!canManage) return <Card title="Webhooks"><Banner tone="info">Webhooks are managed by the workspace&apos;s owners and administrators. Your role cannot see or change them.</Banner></Card>;
  if (!isUuid(workspaceId)) return <Card title="Webhooks"><Banner tone="info">Choose a workspace first.</Banner></Card>;
  if (read.isPending) return <p role="status">Loading the webhooks…</p>;
  if (read.isError) {
    const view = webhooksReadError(read.error, "webhooks");
    return <Card title="Webhooks"><Banner tone={view.kind === "forbidden" ? "info" : "crit"}>{view.text}</Banner></Card>;
  }
  const rows = webhookRows(read.data);
  const show = (answer: SecretAnswer) => {
    const value = shownSecret(answer.secret);
    setSecret(value ? { webhookName: answer.webhookName, value } : null);
    setNoSecret(value ? null : answer.secret === null ? "replay" : "unreadable");
  };
  return (
    <>
      <Card title="Webhooks" aside={<Pill tone="gray">{rows.length} of {WEBHOOKS_MAX}</Pill>}>
        <p>DCLab calls your address when something happens in this workspace, for example when a decision is waiting or a run failed.</p>
        <p className="small muted">{WEBHOOK_SAFETY}</p>
        {secret ? <SecretOnce secret={secret} onHide={() => setSecret(null)} /> : null}
        {noSecret === "replay" ? <Banner tone="warn">Done, but this answer carried no secret (it repeated an earlier request). If you do not have the secret, make a new one.</Banner> : null}
        {noSecret === "unreadable" ? <Banner tone="warn">Done, but the secret in the answer could not be read, so it is not shown. Make a new secret.</Banner> : null}
        {rows.length === 0 ? <div className="empty">No webhook yet. Add one below.</div> : rows.map((row) => <WebhookCard key={row.id} workspaceId={workspaceId} row={row} onSecret={show} />)}
      </Card>
      <Card title="Add a webhook">
        <AddForm workspaceId={workspaceId} full={rows.length >= WEBHOOKS_MAX} onSecret={show} />
      </Card>
    </>
  );
}
