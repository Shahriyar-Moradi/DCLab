import type { ReactNode } from "react";

export type PillTone = "ok" | "warn" | "crit" | "ai" | "det" | "gray";

/** Status/provenance chip. `det` = deterministic rule, `ai` = advisory model output. */
export function Pill({ tone = "gray", children }: { tone?: PillTone; children: ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>;
}
