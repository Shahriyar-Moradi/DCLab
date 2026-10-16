"use client";

import Link from "next/link";
import { useRef } from "react";
import { NEW_PROJECT_STEPS } from "@/lib/application/studio-projects";

/**
 * "New project" on the Projects page (V7 UI-B): a dialog that says what today's four-step wizard asks for, then opens it.
 * Nothing is created here; the wizard page does the writes (with its own keys and checks).
 */
export function NewProjectDialog() {
  const dialog = useRef<HTMLDialogElement>(null);
  return (
    <>
      <button type="button" className="btn primary" aria-haspopup="dialog" onClick={() => dialog.current?.showModal()}>New project</button>
      <dialog ref={dialog} className="studio-cmd-dialog v7-projects-dialog" aria-labelledby="v7-projects-new-title" onClick={(event) => event.target === dialog.current && dialog.current?.close()}>
        <div className="v7-projects-dialog-body">
          <h2 id="v7-projects-new-title">New project</h2>
          <p className="muted">A project is one prediction goal. The wizard asks for four things, one step at a time:</p>
          <ol aria-label="The wizard's steps">
            {NEW_PROJECT_STEPS.map((step) => (
              <li key={step.id}><b>{step.label.replace(/^\d+\s*/, "")}.</b> {step.detail}</li>
            ))}
          </ol>
          <p className="muted">Every step is a form, so it works with AI switched off.</p>
          <form method="dialog" className="toolbar">
            <button type="submit" className="btn">Cancel</button>
            <Link className="btn primary" href="/projects/new">Start the wizard</Link>
          </form>
        </div>
      </dialog>
    </>
  );
}
