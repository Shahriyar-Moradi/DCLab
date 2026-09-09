import { cn } from "@/lib/cn";
import type { ReactNode } from "react";
import { fieldErrorClass, fieldHintClass, fieldLabelClass } from "./control";

export function Field({
  label,
  htmlFor,
  hint,
  error,
  className,
  children,
}: {
  label?: string;
  htmlFor?: string;
  hint?: string;
  error?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn("min-w-0", className)}>
      {label ? (
        <label htmlFor={htmlFor} className={fieldLabelClass}>
          {label}
        </label>
      ) : null}
      {children}
      {error ? (
        <p id={htmlFor ? `${htmlFor}-error` : undefined} className={fieldErrorClass} role="alert">
          {error}
        </p>
      ) : hint ? (
        <p id={htmlFor ? `${htmlFor}-hint` : undefined} className={fieldHintClass}>
          {hint}
        </p>
      ) : null}
    </div>
  );
}
