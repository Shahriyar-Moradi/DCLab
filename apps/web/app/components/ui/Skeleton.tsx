import { cn } from "@/lib/cn";

export function Skeleton({ className, label }: { className?: string; label?: string }) {
  return (
    <div
      className={cn("animate-pulse rounded-md bg-navy-soft", className)}
      aria-hidden={label ? undefined : true}
      role={label ? "status" : undefined}
    >
      {label ? <span className="sr-only">{label}</span> : null}
    </div>
  );
}
