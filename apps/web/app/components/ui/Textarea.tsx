import { cn } from "@/lib/cn";
import type { TextareaHTMLAttributes } from "react";
import { controlClass, controlErrorClass } from "./control";
import { Field } from "./Field";

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement> & {
  label?: string;
  hint?: string;
  error?: string;
};

export function Textarea({ label, hint, error, className, id, ...props }: TextareaProps) {
  const field = (
    <textarea
      id={id}
      aria-invalid={error ? true : undefined}
      aria-describedby={error && id ? `${id}-error` : hint && id ? `${id}-hint` : undefined}
      className={cn(controlClass, "min-h-[6.5rem] py-2", error && controlErrorClass, className)}
      {...props}
    />
  );
  if (!label && !hint && !error) return field;
  return (
    <Field label={label} htmlFor={id} hint={hint} error={error}>
      {field}
    </Field>
  );
}
