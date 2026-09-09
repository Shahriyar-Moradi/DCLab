"use client";

import { cn } from "@/lib/cn";
import { ChevronDown } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { buttonClassName, type ButtonSize, type ButtonVariant } from "./Button";
import { useEscape } from "./overlay";

export type ActionMenuItem = {
  id: string;
  label: string;
  disabled?: boolean;
  destructive?: boolean;
  onSelect: () => void;
};

export function ActionMenu({
  label,
  items,
  variant = "secondary",
  size = "md",
  className,
}: {
  label: string;
  items: ActionMenuItem[];
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const rootRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const menuId = useId();
  const enabledIndexes = useMemo(
    () => items.flatMap((item, index) => (item.disabled ? [] : [index])),
    [items],
  );

  const close = useCallback(() => {
    setOpen(false);
    buttonRef.current?.focus();
  }, []);

  useEscape(open, close);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onPointer);
    return () => window.removeEventListener("mousedown", onPointer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const target = enabledIndexes[Math.min(active, Math.max(enabledIndexes.length - 1, 0))];
    if (target !== undefined) itemRefs.current[target]?.focus();
  }, [active, enabledIndexes, open]);

  function selectActive() {
    const target = enabledIndexes[active];
    if (target === undefined) return;
    items[target]?.onSelect();
    close();
  }

  function onButtonKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "ArrowDown" || event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setActive(0);
      setOpen(true);
    }
  }

  function onMenuKeyDown(event: KeyboardEvent<HTMLUListElement>) {
    const last = Math.max(enabledIndexes.length - 1, 0);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((index) => Math.min(index + 1, last));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((index) => Math.max(index - 1, 0));
    } else if (event.key === "Home") {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setActive(last);
    } else if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      selectActive();
    } else if (event.key === "Tab") {
      close();
    }
  }

  return (
    <div ref={rootRef} className={cn("relative inline-flex", className)}>
      <button
        ref={buttonRef}
        type="button"
        className={buttonClassName({ variant, size })}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={onButtonKeyDown}
      >
        {label}
        <ChevronDown size={16} aria-hidden />
      </button>
      {open ? (
        <ul
          id={menuId}
          role="menu"
          className="absolute right-0 z-40 mt-1 min-w-[12rem] max-w-[calc(100vw-2rem)] rounded-md border border-hairline bg-paper-raised p-1 shadow-md"
          onKeyDown={onMenuKeyDown}
        >
          {items.map((item, index) => (
            <li key={item.id} role="none">
              <button
                ref={(node) => {
                  itemRefs.current[index] = node;
                }}
                type="button"
                role="menuitem"
                disabled={item.disabled}
                tabIndex={open && enabledIndexes[active] === index ? 0 : -1}
                className={cn(
                  "flex w-full rounded-sm px-3 py-2 text-left text-body transition-ui disabled:opacity-50",
                  item.destructive ? "text-oxblood hover:bg-oxblood/10" : "text-ink hover:bg-navy-soft",
                )}
                onClick={() => {
                  item.onSelect();
                  close();
                }}
              >
                {item.label}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
