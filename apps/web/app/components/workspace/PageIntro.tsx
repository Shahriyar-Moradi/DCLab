import type { ReactNode } from "react";

export function WorkspaceShell({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-7xl px-5 py-16 lg:px-8">{children}</div>;
}

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
    <div className="mx-auto max-w-3xl text-center">
      {eyebrow ? <p className="text-eyebrow uppercase text-brand">{eyebrow}</p> : null}
      <h1 className="mt-3 text-4xl font-bold tracking-tight text-ink lg:text-5xl">{title}</h1>
      {subtitle ? <p className="mx-auto mt-4 max-w-2xl text-base leading-7 text-ink-muted">{subtitle}</p> : null}
      {actions ? <div className="mt-6 flex flex-wrap items-center justify-center gap-4">{actions}</div> : null}
    </div>
  );
}

export function Pager({
  offset,
  pageSize,
  total,
  onPrev,
  onNext,
}: {
  offset: number;
  pageSize: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
}) {
  return (
    <div className="mt-6 flex items-center justify-between font-body text-body text-ink">
      <button
        type="button"
        className="rounded-full border border-hairline bg-white px-4 py-2 hover:bg-navy-soft disabled:opacity-40"
        disabled={offset === 0}
        onClick={onPrev}
      >
        Previous
      </button>
      <p className="font-mono text-data">
        {total === 0 ? "0" : `${offset + 1}–${Math.min(offset + pageSize, total)}`} of {total}
      </p>
      <button
        type="button"
        className="rounded-full border border-hairline bg-white px-4 py-2 hover:bg-navy-soft disabled:opacity-40"
        disabled={offset + pageSize >= total}
        onClick={onNext}
      >
        Next
      </button>
    </div>
  );
}

export const fieldControlClass =
  "ml-2 rounded-xl border border-hairline bg-white px-3 py-2 text-sm text-ink";
