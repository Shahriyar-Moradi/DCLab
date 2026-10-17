"use client";

import Link from "next/link";
import { createContext, useContext, useEffect, useState } from "react";
import { Banner } from "@/components/studio/Banner";
import {
  PREVIEW_NOTE, PREVIEW_ROLE_LABEL, VIEW_AS_OPTIONS, canUseViewAs, isPreviewRole, previewGateText, restorePreview, storePreview, type PreviewRole,
} from "@/lib/application/view-as";

type Principal = { capabilities: Record<string, boolean> } | null | undefined;

function tabStorage(): Storage | null {
  try {
    return window.sessionStorage;
  } catch {
    return null;
  }
}

// The page load type is read once per document: a reload ends the preview, moving between screens keeps it.
let loadTypeRead = false;
function initialPreview(): PreviewRole | null {
  let type = "navigate";
  if (!loadTypeRead) {
    loadTypeRead = true;
    const entry = typeof performance !== "undefined" ? performance.getEntriesByType?.("navigation")[0] : undefined;
    type = (entry as PerformanceNavigationTiming | undefined)?.type ?? "navigate";
  }
  return restorePreview(tabStorage(), type);
}

/** The preview role for screens inside the Studio frame (null outside a preview). Display only. */
export const PreviewRoleContext = createContext<PreviewRole | null>(null);
export function usePreviewRole(): PreviewRole | null {
  return useContext(PreviewRoleContext);
}

/** The admin-only preview role (null = the admin's own view). Never sent anywhere; the session is unchanged. */
export function useViewAs(user: Principal) {
  const allowed = canUseViewAs(user);
  const [role, setRole] = useState<PreviewRole | null>(null);
  // Read after mount so the server and the first client render agree.
  useEffect(() => {
    setRole(allowed ? initialPreview() : null);
  }, [allowed]);
  const choose = (next: PreviewRole | null) => {
    setRole(next);
    storePreview(tabStorage(), next);
  };
  return { allowed, role: allowed ? role : null, choose };
}

/** Top-bar control, shown to admins only. */
export function ViewAsControl({ role, onChange }: { role: PreviewRole | null; onChange: (role: PreviewRole | null) => void }) {
  return (
    <span className="view-as">
      <label htmlFor="studio-view-as">View as</label>
      <select id="studio-view-as" value={role ?? ""} onChange={(event) => onChange(isPreviewRole(event.target.value) ? event.target.value : null)}>
        {VIEW_AS_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
      </select>
    </span>
  );
}

/** Says, on every screen of the preview, that nothing changed and the requests still use the admin's account. */
export function ViewAsBanner({ role, onStop }: { role: PreviewRole; onStop: () => void }) {
  return (
    <section className="view-as-banner" aria-label="Preview of another role">
      <Banner actions={<button type="button" className="btn" onClick={onStop}>Stop the preview</button>}>
        <b>{PREVIEW_NOTE}</b> The sidebar and which screens open are shown as someone with the {PREVIEW_ROLE_LABEL[role]} role would see them. The screens still show
        your own access, and every request still uses your admin account.
      </Banner>
    </section>
  );
}

/** In place of a screen the previewed role cannot open. */
export function ViewAsGate({ role, onStop }: { role: PreviewRole; onStop: () => void }) {
  const text = previewGateText(role);
  return (
    <section className="card view-as-gate" aria-labelledby="view-as-gate-title">
      <h2 id="view-as-gate-title">{text.title}</h2>
      <p className="muted">{text.body}</p>
      <div className="toolbar">
        {role === "business_user" ? <Link className="btn primary" href="/outcomes">Open the Business view</Link> : null}
        <button type="button" className="btn" onClick={onStop}>Stop the preview</button>
      </div>
    </section>
  );
}
