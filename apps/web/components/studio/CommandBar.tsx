"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { safeInternalHref } from "./safe-href";

export type CommandResult = { id: string; title: string; detail?: string; kind?: string; href?: string };

export type CommandBarProps = {
  /** Results for the current query. Data hooks arrive in P4.1-A; this component is UI only. */
  results?: CommandResult[];
  query?: string;
  onQueryChange?: (query: string) => void;
  onSelect?: (result: CommandResult) => void;
  placeholder?: string;
  emptyMessage?: string;
  /** Own the global Cmd/Ctrl+K shortcut. Exactly one instance per page should set this. */
  globalShortcut?: boolean;
};

/** Top-bar trigger plus a modal palette. Cmd/Ctrl+K opens; arrows move; Enter picks; Esc closes. */
export function CommandBar({ results = [], query, onQueryChange, onSelect, placeholder = "Search projects, experiments, models, decisions", emptyMessage = "No results.", globalShortcut = false }: CommandBarProps) {
  const router = useRouter();
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const [local, setLocal] = useState("");
  const [index, setIndex] = useState(0);
  const listId = useId();
  const value = query ?? local;

  function open() {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
  }
  function change(next: string) {
    setLocal(next);
    setIndex(0);
    onQueryChange?.(next);
  }
  function pick(result: CommandResult) {
    onSelect?.(result);
    dialog.current?.close();
    const href = safeInternalHref(result.href);
    if (href) router.push(href); // unsafe hrefs (any scheme, //host) are ignored
  }

  useEffect(() => {
    if (!globalShortcut) return;
    const handler = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        open();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [globalShortcut]);

  return (
    <>
      <button ref={trigger} type="button" className="cmd" onClick={open} aria-haspopup="dialog" aria-keyshortcuts="Control+K Meta+K">
        <span aria-hidden="true">⌕</span>
        <span>{placeholder}</span>
        <kbd aria-hidden="true">⌘K</kbd>
      </button>
      <dialog ref={dialog} className="studio-cmd-dialog" aria-label="Command bar" onClose={() => trigger.current?.focus()}>
        <div className="cmd-in">
          <input
            role="combobox"
            aria-expanded="true"
            aria-controls={listId}
            aria-activedescendant={results[index] ? `${listId}-${results[index].id}` : undefined}
            aria-label="Search"
            placeholder={placeholder}
            value={value}
            autoFocus
            onChange={(event) => change(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") { event.preventDefault(); setIndex((i) => Math.min(i + 1, results.length - 1)); }
              else if (event.key === "ArrowUp") { event.preventDefault(); setIndex((i) => Math.max(i - 1, 0)); }
              else if (event.key === "Enter" && results[index]) { event.preventDefault(); pick(results[index]); }
            }}
          />
          <kbd>Esc</kbd>
        </div>
        {results.length === 0 ? (
          <p className="cmd-empty" role="status">{emptyMessage}</p>
        ) : (
          <ul id={listId} role="listbox" aria-label="Results">
            {results.map((result, i) => (
              <li key={result.id} role="presentation">
                <button type="button" role="option" id={`${listId}-${result.id}`} aria-selected={i === index} onClick={() => pick(result)}>
                  <span>{result.title}</span>
                  {result.detail || result.kind ? <small>{[result.kind, result.detail].filter(Boolean).join(" · ")}</small> : null}
                </button>
              </li>
            ))}
          </ul>
        )}
      </dialog>
    </>
  );
}
