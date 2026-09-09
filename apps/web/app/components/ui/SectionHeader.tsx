import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export function SectionHeader({
  title,
  description,
  actions,
  className,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between", className)}>
      <div className="min-w-0">
        <h2 className="min-w-0 break-words font-sans text-section text-ink">{title}</h2>
        {description ? <p className="mt-1 max-w-3xl break-words text-body text-ink-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto">{actions}</div> : null}
    </div>
  );
}
