import { cn } from "@/lib/cn";
import { formatPercent, TONE_FILL, type SignalTone } from "@/lib/domain";

export function ConfidenceBar({
  value,
  tone,
  className,
}: {
  value: number;
  tone: SignalTone;
  className?: string;
}) {
  const width = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <div className="h-1.5 w-full overflow-hidden rounded bg-navy-soft" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={width} aria-label="Confidence">
        <div className="h-full rounded" style={{ width: `${width}%`, background: TONE_FILL[tone] }} />
      </div>
      <span className="shrink-0 font-mono text-data text-ink">{formatPercent(value)}</span>
    </div>
  );
}
