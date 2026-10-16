"use client";

import { useRef } from "react";
import { GLOSSARY, GLOSSARY_ORDER } from "./glossary.ts";

/** Top-bar Glossary button and panel (V7 UI-A): the technical words used on the screens, each in plain words. Text only. */
export function GlossaryDialog() {
  const dialog = useRef<HTMLDialogElement>(null);
  return (
    <>
      <button type="button" className="btn" aria-haspopup="dialog" onClick={() => dialog.current?.showModal()}>Glossary</button>
      <dialog ref={dialog} className="studio-cmd-dialog glossary-dialog" aria-labelledby="studio-glossary-title" onClick={(event) => event.target === dialog.current && dialog.current?.close()}>
        <div className="glossary-body">
          <h2 id="studio-glossary-title">Glossary</h2>
          <p className="muted">The few technical words used on these screens. Dotted words on a screen show the same explanation when you point at them or press them.</p>
          <dl>
            {GLOSSARY_ORDER.map((key) => (
              <div key={key}>
                <dt>{GLOSSARY[key].label}</dt>
                <dd>{GLOSSARY[key].definition}</dd>
              </div>
            ))}
          </dl>
          <form method="dialog">
            <button type="submit" className="btn primary">Close</button>
          </form>
        </div>
      </dialog>
    </>
  );
}
