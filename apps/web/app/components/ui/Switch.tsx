"use client";

import { cn } from "@/lib/cn";
import type { ButtonHTMLAttributes } from "react";

export function Switch({
  checked,
  onCheckedChange,
  label,
  className,
  disabled,
  ...props
}: Omit<ButtonHTMLAttributes<HTMLButtonElement>, "onChange"> & {
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  label?: string;
}) {
  return (
    <div className="inline-flex items-center gap-2.5">
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={props["aria-label"] ?? label}
        disabled={disabled}
        onClick={() => onCheckedChange(!checked)}
        className={cn(
          "relative h-5 w-9 shrink-0 rounded-full transition-ui",
          "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          checked ? "bg-navy" : "bg-hairline",
          disabled && "cursor-not-allowed opacity-50",
          className,
        )}
        {...props}
      >
        <span
          aria-hidden
          className={cn(
            "absolute top-0.5 h-4 w-4 rounded-full bg-paper-raised shadow-sm transition-ui",
            checked ? "left-4" : "left-0.5",
          )}
        />
      </button>
      {label ? <span className="text-body text-ink">{label}</span> : null}
    </div>
  );
}
