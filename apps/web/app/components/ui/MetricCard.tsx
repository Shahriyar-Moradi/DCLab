import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export function MetricCard({
  label,
  value,
  hint,
  icon,
  tone = "default",
  className,
}: {
  label: string;
  value: string;
  hint?: string;
  icon?: ReactNode;
  tone?: "default" | "brand" | "warning";
  className?: string;
}) {
  return (
    <div className={cn("product-metric-card", `product-metric-card-${tone}`, className)}>
      <div className="flex items-start justify-between gap-3">
        <p className="product-eyebrow">{label}</p>
        {icon ? <span className="product-metric-icon">{icon}</span> : null}
      </div>
      <p className="mt-3 break-words font-sans text-kpi text-ink">{value}</p>
      {hint ? <p className="mt-1 text-helper text-ink-muted">{hint}</p> : null}
    </div>
  );
}
