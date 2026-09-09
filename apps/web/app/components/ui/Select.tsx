import { cn } from "@/lib/cn";
import type { SelectHTMLAttributes } from "react";
import { controlClass, controlErrorClass, controlHeightClass } from "./control";
import { Field } from "./Field";

type SelectProps = SelectHTMLAttributes<HTMLSelectElement> & {
  label?: string;
  hint?: string;
  error?: string;
};

export function Select({ label, hint, error, className, id, children, ...props }: SelectProps) {
  const field = (
    <select
      id={id}
      aria-invalid={error ? true : undefined}
      aria-describedby={error && id ? `${id}-error` : hint && id ? `${id}-hint` : undefined}
      className={cn(controlClass, controlHeightClass, error && controlErrorClass, className)}
      {...props}
    >
      {children}
    </select>
  );
  if (!label && !hint && !error) return field;
  return (
    <Field label={label} htmlFor={id} hint={hint} error={error}>
      {field}
    </Field>
  );
}
