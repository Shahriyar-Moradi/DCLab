import { cn } from "@/lib/cn";
import type { SignalTone } from "@/lib/domain";
import type { ReactNode } from "react";
import { Badge } from "./Badge";
import { Breadcrumbs, type BreadcrumbItem } from "./Breadcrumbs";

export type { BreadcrumbItem };

export function PageHeader({
  eyebrow,
  title,
  description,
  breadcrumbs,
  status,
  actions,
  className,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  breadcrumbs?: BreadcrumbItem[];
  status?: { label: string; tone?: SignalTone };
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("product-page-header", className)}>
      {breadcrumbs?.length ? <Breadcrumbs items={breadcrumbs} /> : null}
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          {eyebrow ? <p className="product-eyebrow">{eyebrow}</p> : null}
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <h1 className="font-sans text-title text-ink">{title}</h1>
            {status ? (
              <Badge tone={status.tone ?? "amber"} emphasis="soft">
                {status.label}
              </Badge>
            ) : null}
          </div>
          {description ? <p className="mt-2 max-w-3xl text-body text-ink-muted">{description}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
      </div>
    </header>
  );
}
