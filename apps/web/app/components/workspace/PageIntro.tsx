import type { ReactNode } from "react";

export function PageIntro({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div>
        {eyebrow ? <p className="text-eyebrow uppercase text-brand">{eyebrow}</p> : null}
        <h1 className="mt-1 font-display text-title text-ink">{title}</h1>
        {subtitle ? <p className="mt-2 max-w-2xl font-body text-body text-ink-muted">{subtitle}</p> : null}
      </div>
      {actions}
    </div>
  );
}

export const fieldControlClass =
  "ml-2 rounded-xl border border-hairline bg-paper-raised px-3 py-2 text-sm text-ink";
