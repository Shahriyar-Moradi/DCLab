"use client";

import { useState } from "react";

/** Copies plain text (a snippet, never a secret from storage). Reports success or failure in words. */
export function CopyButton({ text, label }: { text: string; label: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      setState("failed");
    }
  }
  return (
    <span className="toolbar">
      <button type="button" className="btn sm" aria-label={label} onClick={() => void copy()}>
        {state === "copied" ? "Copied" : "Copy"}
      </button>
      <span role="status" className="small muted">
        {state === "failed" ? "Could not copy. Select the text and copy it by hand." : ""}
      </span>
    </span>
  );
}
