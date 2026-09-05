import { cn } from "@/lib/cn";
import type { ReactNode, ThHTMLAttributes } from "react";

export function Table({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div className="relative overflow-x-auto overscroll-x-contain rounded-xl border border-hairline bg-paper-raised shadow-sm">
      <p className="mb-2 px-4 pt-3 font-sans text-label uppercase text-ink-muted md:hidden">
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
      <th className={cn("border-b border-hairline bg-navy-soft/35 px-5 py-3", className)} {...props}>
        <button
          type="button"
          onClick={onSort}
          className="font-sans text-label uppercase text-ink-muted transition-ui hover:text-ink"
        >
          {children}
        </button>
      </th>
    );
  }
  return (
    <th
      className={cn(
        "border-b border-hairline bg-navy-soft/35 px-5 py-3 font-sans text-label uppercase text-ink-muted",
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
    <td
      className={cn(
        "max-w-[18rem] break-words border-b border-hairline px-5 py-3.5 align-top font-sans text-body text-ink",
        mono && "break-all font-mono text-data",
        className,
      )}
    >
      {children}
    </td>
  );
}
