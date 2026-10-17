"use client";

/**
 * D8b: Settings → Notifications. Your own email choices in the selected workspace, from `GET /v1/me/notification-preferences`;
 * Save sends only the kinds you changed (`PUT`, Idempotency-Key, single flight). Whether this server sends email at all and
 * whether your role receives a kind come from the read; every sentence is fixed (the server's note is not shown).
 */
import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Banner } from "@/components/studio/Banner";
import { Card } from "@/components/studio/Card";
import { Pill } from "@/components/studio/Pill";
import { changedPreferences, deliveryBanners, preferenceRows, preferenceSaveError, preferencesReadError } from "@/lib/application/studio-notifications";
import { saveNotificationPreferences, useNotificationPreferences } from "@/lib/application/studio-notifications-hooks";
import { ActionKeys } from "@/lib/application/studio-wizard";
import { workspaceQueryKey } from "@/lib/infrastructure/active-workspace";
import { newIdempotencyKey } from "@/lib/infrastructure/v1/client";

export function NotificationsPanel({ ready }: { ready: boolean }) {
  const read = useNotificationPreferences(ready);
  const client = useQueryClient();
  const keys = useRef(new ActionKeys(newIdempotencyKey)).current;
  const flight = useRef(false);
  const [draft, setDraft] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  if (!ready || read.isPending) return <p role="status">Loading your email choices…</p>;
  if (read.isError) {
    const view = preferencesReadError(read.error);
    return view.kind === "forbidden" ? <Banner tone="info">{view.text}</Banner> : <Banner tone="crit">{view.text}</Banner>;
  }
  const rows = preferenceRows(read.data);
  const changes = changedPreferences(rows, draft);
  const save = async () => {
    if (flight.current || changes.length === 0) return;
    flight.current = true;
    setBusy(true); setError(null); setSaved(false);
    try {
      const next = await saveNotificationPreferences(changes, keys.keyFor("save", JSON.stringify(changes)));
      keys.done("save");
      client.setQueryData(workspaceQueryKey("v1", "notification-preferences"), next);
      setDraft({});
      setSaved(true);
    } catch (caught) {
      setError(preferenceSaveError(caught));
    } finally {
      flight.current = false;
      setBusy(false);
    }
  };
  return (
    <Card title="Email notifications" aside={<Pill tone="gray">only for you</Pill>}>
      {deliveryBanners(read.data).map((b) => <Banner key={b.text} tone={b.tone}>{b.text}</Banner>)}
      <p className="muted">Choose which emails DCLab sends you about this workspace. These choices are yours only. In-app items (Inbox, Home) are not affected.</p>
      {rows.length === 0 ? <div className="empty">There are no kinds of email to choose from.</div> : (
        <form aria-label="Email notifications" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <ul className="plain-list" aria-label="Kinds of email">
            {rows.map((row) => {
              const on = Object.hasOwn(draft, row.kind) ? draft[row.kind] === true : row.on;
              return (
                <li key={row.kind} data-kind={row.kind} style={{ padding: "8px 0" }}>
                  <label className="toolbar" style={{ alignItems: "flex-start" }}>
                    <input type="checkbox" checked={on} disabled={busy} onChange={(e) => { setSaved(false); setDraft((d) => ({ ...d, [row.kind]: e.target.checked })); }} />
                    <span>
                      <b>{row.label}</b>{" "}
                      {row.applies ? null : <Pill tone="gray">not for your role</Pill>}
                      <br /><span className="small muted">{row.who}{row.note ? ` ${row.note}` : ""}{row.isDefault ? "" : " Changed from the default."}</span>
                    </span>
                  </label>
                </li>
              );
            })}
          </ul>
          {error ? <Banner tone="crit">{error}</Banner> : null}
          {saved ? <p role="status">Saved. Your email choices are updated.</p> : null}
          <div className="toolbar">
            <button type="submit" className="btn primary" disabled={busy || changes.length === 0}>{busy ? "Saving…" : changes.length ? `Save ${changes.length} ${changes.length === 1 ? "change" : "changes"}` : "No changes to save"}</button>
            {changes.length ? <button type="button" className="btn" disabled={busy} onClick={() => setDraft({})}>Undo changes</button> : null}
          </div>
        </form>
      )}
    </Card>
  );
}
