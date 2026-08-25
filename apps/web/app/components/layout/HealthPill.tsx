"use client";

import { useHealth } from "@/lib/application";

export function HealthPill() {
  const health = useHealth();
  const connected = health.data?.status === "ok";
  return (
    <p className="inline-flex items-center gap-2 font-body text-body text-ink" aria-live="polite">
      <span
        className={connected ? "h-2 w-2 rounded-full bg-green" : "h-2 w-2 rounded-full bg-oxblood"}
        aria-hidden
      />
      {health.isPending ? "Checking backend…" : connected ? "Connected" : "Backend unreachable"}
    </p>
  );
}
