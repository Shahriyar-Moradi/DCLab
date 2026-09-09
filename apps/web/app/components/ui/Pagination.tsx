import { cn } from "@/lib/cn";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { IconButton } from "./IconButton";

export function Pagination({
  page,
  pageCount,
  onPageChange,
  disabled = false,
  className,
}: {
  page: number;
  pageCount: number;
  onPageChange: (page: number) => void;
  disabled?: boolean;
  className?: string;
}) {
  const safeCount = Math.max(1, pageCount);
  const current = Math.min(Math.max(1, page), safeCount);
  return (
    <nav
      className={cn("flex items-center justify-between gap-3", className)}
      aria-label="Pagination"
    >
      <p className="text-helper text-ink-muted">
        Page {current} of {safeCount}
      </p>
      <div className="flex items-center gap-1">
        <IconButton
          label="Previous page"
          disabled={disabled || current <= 1}
          onClick={() => onPageChange(current - 1)}
        >
          <ChevronLeft size={18} aria-hidden />
        </IconButton>
        <IconButton
          label="Next page"
          disabled={disabled || current >= safeCount}
          onClick={() => onPageChange(current + 1)}
        >
          <ChevronRight size={18} aria-hidden />
        </IconButton>
      </div>
    </nav>
  );
}
