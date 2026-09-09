import { cn } from "@/lib/cn";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Spinner } from "./Spinner";

export function IconButton({
  label,
  className,
  loading = false,
  disabled,
  children,
  type = "button",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  loading?: boolean;
  children: ReactNode;
}) {
  return (
    <button
      type={type}
      aria-label={label}
      title={label}
      disabled={Boolean(disabled || loading)}
      aria-busy={loading || undefined}
      className={cn(
        "inline-flex h-9 w-9 items-center justify-center rounded-md text-ink-muted transition-ui",
        "hover:bg-navy-soft hover:text-ink",
        "disabled:pointer-events-none disabled:opacity-50",
        className,
      )}
      {...props}
    >
      {loading ? <Spinner label={label} /> : children}
    </button>
  );
}
