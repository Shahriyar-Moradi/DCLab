import { cn } from "@/lib/cn";
import { TONE_BG, type SignalTone } from "@/lib/domain";

export function Badge({
  tone = "amber",
  children,
  className,
}: {
  tone?: SignalTone;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded px-2 py-1 font-body text-eyebrow uppercase tracking-[0.06em] text-paper-raised",
        TONE_BG[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
