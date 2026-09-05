import { cn } from "@/lib/cn";
import { Spinner } from "./Spinner";

export function LoadingState({
  label = "Loading",
  className,
}: {
  label?: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex min-h-40 flex-col items-center justify-center gap-3 rounded-xl border border-hairline bg-paper-raised px-6 py-12 text-body text-ink-muted",
        className,
      )}
      role="status"
      aria-live="polite"
    >
      <Spinner label={label} />
      <p>{label}</p>
    </div>
  );
}
