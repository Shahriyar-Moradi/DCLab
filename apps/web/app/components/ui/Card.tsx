import { cn } from "@/lib/cn";
import type { HTMLAttributes, ReactNode } from "react";

export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn("rounded-xl border border-hairline bg-paper-raised shadow-xs", className)}
      {...props}
    />
  );
}

export function Panel({
  title,
  description,
  actions,
  className,
  children,
  ...props
}: HTMLAttributes<HTMLDivElement> & {
  title?: string;
  description?: string;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <Card className={cn("p-5", className)} {...props}>
      {title || description || actions ? (
        <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            {title ? <h2 className="font-sans text-section text-ink">{title}</h2> : null}
            {description ? <p className="mt-1 text-body text-ink-muted">{description}</p> : null}
          </div>
          {actions}
        </div>
      ) : null}
      {children}
    </Card>
  );
}
