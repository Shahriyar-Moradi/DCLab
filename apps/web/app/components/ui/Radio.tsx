import { cn } from "@/lib/cn";
import type { InputHTMLAttributes, ReactNode } from "react";

export function Radio({
  label,
  className,
  id,
  ...props
}: InputHTMLAttributes<HTMLInputElement> & { label?: ReactNode }) {
  const input = (
    <input
      id={id}
      type="radio"
      className={cn(
        "h-4 w-4 shrink-0 border-hairline accent-navy disabled:cursor-not-allowed disabled:opacity-50",
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

export function RadioGroup({
  legend,
  error,
  className,
  children,
}: {
  legend: string;
  error?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <fieldset className={cn("min-w-0", className)}>
      <legend className="mb-2 font-sans text-body font-medium text-ink">{legend}</legend>
      <div className="grid gap-2">{children}</div>
      {error ? (
        <p className="mt-1.5 text-helper text-oxblood" role="alert">
          {error}
        </p>
      ) : null}
    </fieldset>
  );
}
