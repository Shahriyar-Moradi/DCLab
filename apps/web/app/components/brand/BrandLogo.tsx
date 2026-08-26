import Link from "next/link";
import { cn } from "@/lib/cn";

export function BrandLogo({ className }: { className?: string }) {
  return (
    <Link href="/" className={cn("flex items-center gap-2.5", className)} aria-label="Decision.ai home">
      <svg width="28" height="28" viewBox="0 0 28 28" fill="none" aria-hidden>
        <circle cx="14" cy="6" r="3.2" fill="#2563EB" />
        <circle cx="6.5" cy="20" r="3.2" fill="#38BDF8" />
        <circle cx="21.5" cy="20" r="3.2" fill="#2563EB" />
        <path d="M14 9.2L8.4 18.2M14 9.2L19.6 18.2M8.8 20h10.4" stroke="#38BDF8" strokeWidth="1.6" />
      </svg>
      <span className="leading-tight">
        <span className="block text-[1.05rem] font-bold tracking-tight text-ink">Decision.ai</span>
        <span className="block text-[0.62rem] font-semibold uppercase tracking-[0.12em] text-ink-muted">
          Decision Intelligence
        </span>
      </span>
    </Link>
  );
}
