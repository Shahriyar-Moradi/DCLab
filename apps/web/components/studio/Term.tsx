"use client";

import { useId, useState, type ReactNode } from "react";

/** Glossary primitive (plain-text definition only, never HTML): dotted term with an explanation on hover, focus or Enter/Space. Esc closes. */
export function Term({ children, definition }: { children: ReactNode; definition: string }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  return (
    <button
      type="button"
      className="term"
      aria-describedby={id}
      aria-expanded={open}
      onClick={() => setOpen((value) => !value)}
      onBlur={() => setOpen(false)}
      onKeyDown={(event) => event.key === "Escape" && setOpen(false)}
    >
      {children}
      <span className="tip" role="tooltip" id={id}>
        {definition}
      </span>
    </button>
  );
}
