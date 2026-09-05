import { cn } from "@/lib/cn";
import type { ReactNode } from "react";

export function Tooltip({
  content,
  children,
  className,
}: {
  content: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <span className={cn("group relative inline-flex", className)}>
      {children}
      <span
        role="tooltip"
        className="pointer-events-none absolute bottom-[calc(100%+0.4rem)] left-1/2 z-40 hidden w-max max-w-xs -translate-x-1/2 rounded-md bg-midnight px-2 py-1 text-helper text-paper-raised shadow-md group-hover:block group-focus-within:block"
      >
        {content}
      </span>
    </span>
  );
}
