import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export function GlassPanel({
  title,
  description,
  children,
  className,
}: {
  title?: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("product-glass-panel", className)}>
      {title ? <h2 className="min-w-0 break-words font-sans text-section text-ink">{title}</h2> : null}
      {description ? <p className="mt-1 break-words text-body text-ink-muted">{description}</p> : null}
      <div className={title || description ? "mt-5" : undefined}>{children}</div>
    </section>
  );
}
