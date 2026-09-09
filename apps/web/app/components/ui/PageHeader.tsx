import { cn } from "@/lib/cn";
import type { SignalTone } from "@/lib/domain";
import type { ReactNode } from "react";
import { Badge } from "./Badge";
import { Breadcrumbs, type BreadcrumbItem } from "./Breadcrumbs";

export type { BreadcrumbItem };

export function PageHeader({
  eyebrow,
  title,
  identifier,
  description,
  breadcrumbs,
  status,
  actions,
  className,
}: {
  eyebrow?: string;
  title: string;
  identifier?: string;
  description?: string;
  breadcrumbs?: BreadcrumbItem[];
  status?: { label: string; tone?: SignalTone };
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cn("product-page-header", className)}>
      {breadcrumbs?.length ? <Breadcrumbs items={breadcrumbs} /> : null}
      <div className="product-page-header-row">
        <div className="min-w-0">
          {eyebrow ? <p className="product-eyebrow">{eyebrow}</p> : null}
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <h1 className="min-w-0 break-words font-sans text-title text-ink">{title}</h1>
            {status ? (
              <Badge tone={status.tone ?? "amber"} emphasis="soft">
                {status.label}
              </Badge>
            ) : null}
          </div>
          {identifier ? (
            <p className="mt-1 break-all font-mono text-data text-ink-muted">{identifier}</p>
          ) : null}
          {description ? <p className="mt-2 max-w-3xl break-words text-body text-ink-muted">{description}</p> : null}
        </div>
        {actions ? <div className="flex w-full min-w-0 flex-wrap items-center gap-2 sm:w-auto sm:justify-end">{actions}</div> : null}
      </div>
    </header>
  );
}
