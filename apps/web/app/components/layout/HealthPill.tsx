"use client";

import { cn } from "@/lib/cn";
import { useHealth } from "@/lib/application";

export function HealthPill({ invert = false }: { invert?: boolean }) {
  const health = useHealth();
  const pending = health.isPending;
  const connected = health.data?.status === "ok";
  const label = pending ? "Checking backend…" : connected ? "Connected" : "Backend unreachable";
  return (
    <p
      className={cn("inline-flex items-center gap-2 text-body", invert ? "text-white/80" : "text-ink")}
      aria-live="polite"
    >
      <span
        className={pending ? "h-2 w-2 rounded-full bg-amber" : connected ? "h-2 w-2 rounded-full bg-green" : "h-2 w-2 rounded-full bg-oxblood"}
        aria-hidden
      />
      {label}
    </p>
  );
}
