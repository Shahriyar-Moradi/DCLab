import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

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
  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between", className)}>
      <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={ariaLabel}>
        {options.map((option) => {
          const active = option.id === value;
          return (
            <button
              key={option.id}
              type="button"
              disabled={option.disabled}
              aria-pressed={active}
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
      {trailing ? <div className="min-w-0 sm:w-64">{trailing}</div> : null}
    </div>
  );
}
