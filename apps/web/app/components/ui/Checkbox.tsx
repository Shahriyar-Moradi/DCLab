import { cn } from "@/lib/cn";
import type { InputHTMLAttributes, ReactNode } from "react";

export function Checkbox({
  label,
  className,
  id,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label?: ReactNode }) {
  const input = (
    <input
      id={id}
      type="checkbox"
      className={cn(
        "h-4 w-4 shrink-0 rounded-sm border-hairline accent-navy disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    />
  );
  if (!label) return input;
  return (
    <label htmlFor={id} className="inline-flex items-start gap-2.5 text-body text-ink">
      {input}
      <span>{label}</span>
    </label>
  );
}
