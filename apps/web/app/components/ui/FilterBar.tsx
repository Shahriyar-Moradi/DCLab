"use client";

import { cn } from "@/lib/cn";
import type { KeyboardEvent, ReactNode } from "react";

export type FilterOption = {
  id: string;
  label: string;
  disabled?: boolean;
};

export function FilterBar({
  options,
  value,
  onChange,
  trailing,
  className,
  ariaLabel = "Filters",
}: {
  options: FilterOption[];
  value: string;
  onChange: (id: string) => void;
  trailing?: ReactNode;
  className?: string;
  ariaLabel?: string;
}) {
  const enabled = options.filter((option) => !option.disabled);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const index = enabled.findIndex((option) => option.id === value);
    if (index < 0 || enabled.length === 0) return;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      onChange(enabled[(index + 1) % enabled.length].id);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      onChange(enabled[(index - 1 + enabled.length) % enabled.length].id);
    } else if (event.key === "Home") {
      event.preventDefault();
      onChange(enabled[0].id);
    } else if (event.key === "End") {
      event.preventDefault();
      onChange(enabled[enabled.length - 1].id);
    }
  }

  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between", className)}>
      <div
        className="flex min-w-0 flex-wrap items-center gap-1.5"
        role="group"
        aria-label={ariaLabel}
        onKeyDown={onKeyDown}
      >
        {options.map((option) => {
          const active = option.id === value;
          return (
            <button
              key={option.id}
              type="button"
              aria-pressed={active}
              disabled={option.disabled}
              onClick={() => onChange(option.id)}
              className={cn(
                "h-8 rounded-md px-3 text-button transition-ui disabled:opacity-50",
                active
                  ? "bg-navy-soft text-ink"
                  : "border border-hairline bg-paper-raised text-ink-muted hover:text-ink",
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      {trailing ? <div className="min-w-0 sm:min-w-64 sm:flex-1 sm:max-w-md">{trailing}</div> : null}
    </div>
  );
}
