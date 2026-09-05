import { cn } from "@/lib/cn";
import type { InputHTMLAttributes } from "react";
import { controlClass, controlErrorClass, controlHeightClass } from "./control";
import { Field } from "./Field";

type InputProps = InputHTMLAttributes<HTMLInputElement> & {
  label?: string;
  hint?: string;
  error?: string;
};

export function Input({ label, hint, error, className, id, ...props }: InputProps) {
  const input = (
    <input
      id={id}
      aria-invalid={error ? true : undefined}
      aria-describedby={error && id ? `${id}-error` : hint && id ? `${id}-hint` : undefined}
      className={cn(controlClass, controlHeightClass, error && controlErrorClass, className)}
      {...props}
    />
  );
  if (!label && !hint && !error) return input;
  return (
    <Field label={label} htmlFor={id} hint={hint} error={error}>
      {input}
    </Field>
  );
}
