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
    <div className="rounded bg-paper-raised px-8 py-16 text-center">
      <h2 className="font-display text-section text-ink">{title}</h2>
      <p className="mx-auto mt-3 max-w-md font-body text-body text-ink-muted">{body}</p>
      {actionHref ? (
        <Link
          href={actionHref}
          className="mt-6 inline-flex items-center justify-center rounded bg-navy px-4 py-2 font-body text-body font-medium text-paper-raised hover:bg-navy/90"
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
