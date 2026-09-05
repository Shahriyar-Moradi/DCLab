"use client";

import { cn } from "@/lib/cn";
import type { KeyboardEvent, ReactNode } from "react";

export type TabItem = {
  id: string;
  label: string;
  disabled?: boolean;
};

export function Tabs({
  items,
  value,
  onChange,
  className,
}: {
  items: TabItem[];
  value: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const enabled = items.filter((item) => !item.disabled);
    const index = enabled.findIndex((item) => item.id === value);
    if (index < 0) return;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      onChange(enabled[(index + 1) % enabled.length].id);
    }
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      onChange(enabled[(index - 1 + enabled.length) % enabled.length].id);
    }
    if (event.key === "Home") {
      event.preventDefault();
      onChange(enabled[0].id);
    }
    if (event.key === "End") {
      event.preventDefault();
      onChange(enabled[enabled.length - 1].id);
    }
  }

  return (
    <div
      role="tablist"
      className={cn("flex flex-wrap gap-1.5 border-b border-hairline", className)}
      onKeyDown={onKeyDown}
    >
      {items.map((item) => {
        const selected = item.id === value;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            id={`tab-${item.id}`}
            aria-selected={selected}
            aria-controls={`tab-panel-${item.id}`}
            tabIndex={selected ? 0 : -1}
            disabled={item.disabled}
            onClick={() => onChange(item.id)}
            className={cn(
              "-mb-px border-b-2 px-3 py-2 text-button transition-ui disabled:opacity-50",
              selected
                ? "border-navy text-ink"
                : "border-transparent text-ink-muted hover:text-ink",
            )}
          >
            {item.label}
          </button>
        );
      })}
    </div>
  );
}

export function TabPanel({
  id,
  value,
  children,
  className,
}: {
  id: string;
  value: string;
  children: ReactNode;
  className?: string;
}) {
  if (id !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`tab-panel-${id}`}
      aria-labelledby={`tab-${id}`}
      className={className}
    >
      {children}
    </div>
  );
}
