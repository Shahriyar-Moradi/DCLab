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
        className="pointer-events-none invisible absolute bottom-[calc(100%+0.4rem)] left-1/2 z-40 w-max max-w-xs -translate-x-1/2 break-words rounded-md bg-midnight px-2 py-1 text-helper text-paper-raised opacity-0 shadow-md group-hover:visible group-hover:opacity-100 group-focus-within:visible group-focus-within:opacity-100"
      >
        {content}
      </span>
    </span>
  );
}
