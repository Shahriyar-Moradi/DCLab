"use client";

import { useSyncExternalStore, type ReactNode } from "react";
import { guideHiddenSnapshot, setGuideHidden, subscribeGuide } from "../../lib/application/guide-preference.ts";

export type PageGuideProps = {
  purpose: ReactNode;
  howTo: ReactNode;
  youGet: ReactNode;
  attention?: ReactNode;
  label?: string;
};

/** Keep keyboard focus on the control that replaced the one just pressed. */
function toggle(hidden: boolean) {
  setGuideHidden(hidden);
  requestAnimationFrame(() => document.querySelector<HTMLButtonElement>(hidden ? ".guide-off button" : ".guide .guide-hide")?.focus());
}

/**
 * Per-screen orientation in plain words: what this is, what to do, what you get, what needs attention.
 * It can be hidden; the choice is remembered in this browser for every screen. "Needs attention" stays visible when hidden.
 */
export function PageGuide({ purpose, howTo, youGet, attention, label = "About this screen" }: PageGuideProps) {
  // The server and the first client render show the guide; the stored choice applies right after.
  const hidden = useSyncExternalStore(subscribeGuide, () => guideHiddenSnapshot(), () => false);
  if (hidden) {
    return (
      <section className="guide-off" aria-label={label}>
        {attention ? <p><b>Needs attention:</b> {attention}</p> : null}
        <button type="button" className="btn" aria-expanded="false" onClick={() => toggle(false)}>Show the guide</button>
      </section>
    );
  }
  const sections: Array<[string, ReactNode]> = [
    ["What this is", purpose],
    ["What to do", howTo],
    ["What you get", youGet],
  ];
  if (attention) sections.push(["Needs attention", attention]);
  return (
    <section className="guide" aria-label={label}>
      {sections.map(([title, body]) => (
        <div key={title}>
          <h2>{title}</h2>
          <p>{body}</p>
        </div>
      ))}
      <button type="button" className="guide-hide linkish" aria-expanded="true" onClick={() => toggle(true)}>Hide<span className="sr-only"> the guide</span></button>
    </section>
  );
}
