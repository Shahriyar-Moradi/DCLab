"use client";

import { commandDestinationsForRole, type CommandDestination } from "@/app/components/layout/app-navigation";
import { filterByText } from "@/app/components/ui/localCollection";
import { useBodyScrollLock, useEscape, useFocusTrap } from "@/app/components/ui/overlay";
import { useSession } from "@/lib/application";
import { Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

export function CommandPalette({
  open,
  onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const { user } = useSession();
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const destinations = useMemo(() => commandDestinationsForRole(user), [user]);
  const matches = useMemo(
    () => filterByText(destinations, query, (item) => [item.label, item.group, item.href]),
    [destinations, query],
  );

  useBodyScrollLock(open);
  useEscape(open, onClose);
  useFocusTrap(open, panelRef);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActive(0);
    inputRef.current?.focus();
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  function go(item: CommandDestination) {
    onClose();
    router.push(item.href);
  }

  if (!open || typeof document === "undefined") return null;

  return createPortal(
    <div className="app-command-palette">
      <button type="button" className="app-command-palette-backdrop" aria-label="Close search" onClick={onClose} />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="app-command-palette-title"
        className="app-command-palette-panel"
      >
        <h2 id="app-command-palette-title" className="sr-only">
          Search destinations
        </h2>
        <div className="app-command-palette-field">
          <Search size={16} aria-hidden />
          <input
            ref={inputRef}
            id="app-command-palette-input"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Filter destinations"
            aria-label="Filter destinations"
            aria-controls="app-command-palette-results"
            aria-activedescendant={matches[active] ? `app-command-option-${active}` : undefined}
            role="combobox"
            aria-expanded
            aria-autocomplete="list"
            onKeyDown={(event) => {
              if (event.key === "ArrowDown") {
                event.preventDefault();
                setActive((index) => Math.min(index + 1, Math.max(matches.length - 1, 0)));
              } else if (event.key === "ArrowUp") {
                event.preventDefault();
                setActive((index) => Math.max(index - 1, 0));
              } else if (event.key === "Enter") {
                event.preventDefault();
                const item = matches[active];
                if (item) go(item);
              }
            }}
          />
        </div>
        <ul id="app-command-palette-results" role="listbox" aria-label="Destinations" className="app-command-palette-results">
          {matches.length === 0 ? (
            <li className="app-command-palette-empty">No matching destinations.</li>
          ) : (
            matches.map((item, index) => (
              <li key={item.href} role="presentation">
                <button
                  id={`app-command-option-${index}`}
                  type="button"
                  role="option"
                  aria-selected={index === active}
                  className="app-command-palette-item"
                  onMouseEnter={() => setActive(index)}
                  onClick={() => go(item)}
                >
                  <span>{item.label}</span>
                  <span className="app-command-palette-group">{item.group}</span>
                </button>
              </li>
            ))
          )}
        </ul>
      </div>
    </div>,
    document.body,
  );
}

export function useCommandPaletteShortcut(onToggle: () => void) {
  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (!(event.metaKey || event.ctrlKey) || event.key.toLowerCase() !== "k") return;
      if (event.repeat) return;
      event.preventDefault();
      onToggle();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onToggle]);
}
