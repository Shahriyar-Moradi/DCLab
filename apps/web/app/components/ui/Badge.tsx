import { cn } from "@/lib/cn";
import { TONE_BG, type SignalTone } from "@/lib/domain";
import type { ReactNode } from "react";

export type BadgeTone = SignalTone | "neutral";

const SOFT: Record<BadgeTone, string> = {
  green: "bg-green/10 text-green",
  amber: "bg-amber/10 text-amber",
  oxblood: "bg-oxblood/10 text-oxblood",
  neutral: "bg-navy-soft text-ink-muted",
};

export function Badge({
  tone = "amber",
  emphasis = "solid",
  children,
  className,
}: {
  tone?: BadgeTone;
  emphasis?: "solid" | "soft";
  children: ReactNode;
  className?: string;
}) {
  const solid =
    tone === "neutral" ? "bg-navy-soft text-ink" : cn(TONE_BG[tone], "text-paper-raised");
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-md px-2 py-1 font-sans text-label uppercase",
        emphasis === "soft" ? SOFT[tone] : solid,
        className,
      )}
    >
      {children}
    </span>
  );
}
