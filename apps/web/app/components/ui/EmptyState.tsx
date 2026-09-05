import Link from "next/link";
import { buttonClassName } from "./Button";
import { Button } from "./Button";
import { Card } from "./Card";

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
    <Card className="px-8 py-16 text-center">
      <h2 className="font-sans text-section text-ink">{title}</h2>
      <p className="mx-auto mt-3 max-w-md font-sans text-body text-ink-muted">{body}</p>
      {actionHref && actionLabel ? (
        <Link href={actionHref} className={buttonClassName({ className: "mt-6" })}>
          {actionLabel}
        </Link>
      ) : actionLabel && onAction ? (
        <Button className="mt-6" onClick={onAction}>
          {actionLabel}
        </Button>
      ) : null}
    </Card>
  );
}
