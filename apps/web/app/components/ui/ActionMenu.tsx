"use client";

import { cn } from "@/lib/cn";
import { ChevronDown } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
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
  const rootRef = useRef<HTMLDivElement>(null);
  const menuId = useId();
  useEscape(open, () => setOpen(false));

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    window.addEventListener("mousedown", onPointer);
    return () => window.removeEventListener("mousedown", onPointer);
  }, [open]);

  return (
    <div ref={rootRef} className={cn("relative inline-flex", className)}>
      <button
        type="button"
        className={buttonClassName({ variant, size })}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={menuId}
        onClick={() => setOpen((current) => !current)}
      >
        {label}
        <ChevronDown size={16} aria-hidden />
      </button>
      {open ? (
        <ul
          id={menuId}
          role="menu"
          className="absolute right-0 z-40 mt-1 min-w-[12rem] rounded-md border border-hairline bg-paper-raised p-1 shadow-md"
        >
          {items.map((item) => (
            <li key={item.id} role="none">
              <button
                type="button"
                role="menuitem"
                disabled={item.disabled}
                className={cn(
                  "flex w-full rounded-sm px-3 py-2 text-left text-body transition-ui disabled:opacity-50",
                  item.destructive ? "text-oxblood hover:bg-oxblood/10" : "text-ink hover:bg-navy-soft",
                )}
                onClick={() => {
                  item.onSelect();
                  setOpen(false);
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
