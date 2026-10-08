import type { ReactNode } from "react";

export type BannerTone = "info" | "ai" | "warn" | "crit";

/** Inline notice. `ai` marks advisory model output; use `role="alert"` only for crit. */
export function Banner({ tone = "info", actions, children }: { tone?: BannerTone; actions?: ReactNode; children: ReactNode }) {
  return (
    <div className={`banner ${tone === "info" ? "" : tone}`} role={tone === "crit" ? "alert" : "note"}>
      <div>{children}</div>
      {actions ? <div className="toolbar">{actions}</div> : null}
    </div>
  );
}
