import Link from "next/link";
import { cn } from "@/lib/cn";

export function BrandLogo({
  className,
  href = "/",
  product = false,
  compact = false,
  invert = false,
}: {
  className?: string;
  href?: string;
  product?: boolean;
  compact?: boolean;
  invert?: boolean;
}) {
  return (
    <Link href={href} className={cn("flex items-center gap-2.5", className)} aria-label="DCLab home">
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden>
        <circle cx="14" cy="6" r="3.2" fill="#2563EB" />
        <circle cx="6.5" cy="20" r="3.2" fill="#38BDF8" />
        <circle cx="21.5" cy="20" r="3.2" fill="#2563EB" />
        <path d="M14 9.2L8.4 18.2M14 9.2L19.6 18.2M8.8 20h10.4" stroke="#38BDF8" strokeWidth="1.6" />
      </svg>
      <span className="brand-wordmark leading-tight">
        <span
          className={cn(
            "block tracking-tight",
            invert ? "text-white" : "text-ink",
            product ? "text-[1.0625rem] font-semibold" : "text-[1.05rem] font-bold",
          )}
        >
          DCLab
        </span>
        {compact ? null : (
          <span
            className={cn(
              "block",
              invert ? "text-white/65" : "text-ink-muted",
              product
                ? "text-[0.75rem] font-medium tracking-normal"
                : "text-[0.62rem] font-semibold uppercase tracking-[0.18em]",
            )}
          >
            Decision Intelligence
          </span>
        )}
      </span>
    </Link>
  );
}
