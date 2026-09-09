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

export function Fact({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="min-w-0">
      <p className="product-eyebrow">{label}</p>
      <p className={cn("mt-1 break-words text-ink", mono ? "break-all font-mono text-data" : "text-body")}>{value}</p>
    </div>
  );
}

export function FactGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn("grid gap-4 sm:grid-cols-2 xl:grid-cols-3", className)}>{children}</div>;
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
