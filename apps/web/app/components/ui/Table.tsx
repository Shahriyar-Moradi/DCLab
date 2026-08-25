import { cn } from "@/lib/cn";
import type { ReactNode, ThHTMLAttributes } from "react";

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="relative overflow-x-auto">
      <p className="mb-2 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted md:hidden">
        Scroll horizontally to see all columns
      </p>
      <table className={cn("w-full min-w-[640px] border-collapse text-left", className)}>{children}</table>
    </div>
  );
}

export function Th({
  children,
  sortable,
  onSort,
  className,
  ...props
}: ThHTMLAttributes<HTMLTableCellElement> & { sortable?: boolean; onSort?: () => void }) {
  if (sortable) {
    return (
      <th className={cn("border-b border-hairline px-3 py-3", className)} {...props}>
        <button
          type="button"
          onClick={onSort}
          className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted hover:text-ink"
        >
          {children}
        </button>
      </th>
    );
  }
  return (
    <th
      className={cn(
        "border-b border-hairline px-3 py-3 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted",
        className,
      )}
      {...props}
    >
      {children}
    </th>
  );
}

export function Td({ children, className, mono }: { children: ReactNode; className?: string; mono?: boolean }) {
  return (
    <td className={cn("border-b border-hairline px-3 py-3 font-body text-body text-ink", mono && "font-mono text-data", className)}>
      {children}
    </td>
  );
}
