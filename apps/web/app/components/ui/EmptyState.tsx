import Link from "next/link";
import { Button } from "./Button";

export function EmptyState({
  title,
  body,
  actionLabel,
  onAction,
  actionHref,
}: {
  title: string;
  body: string;
  actionLabel?: string;
  onAction?: () => void;
  actionHref?: string;
}) {
  return (
    <div className="rounded-2xl bg-paper-raised px-8 py-16 text-center shadow-sm ring-1 ring-hairline">
      <h2 className="font-display text-section text-ink">{title}</h2>
      <p className="mx-auto mt-3 max-w-md font-body text-body text-ink-muted">{body}</p>
      {actionHref ? (
        <Link
          href={actionHref}
          className="bg-brand-gradient shadow-brand mt-6 inline-flex items-center justify-center rounded-full px-5 py-2.5 font-body text-body font-semibold text-white"
        >
          {actionLabel}
        </Link>
      ) : actionLabel && onAction ? (
        <Button className="mt-6" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </div>
  );
}
