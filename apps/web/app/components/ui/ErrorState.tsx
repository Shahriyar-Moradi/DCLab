import { Button } from "./Button";

export function ErrorState({
  title = "Something went wrong",
  body,
  onRetry,
}: {
  title?: string;
  body: string;
  onRetry?: () => void;
}) {
  return (
    <div role="alert" className="rounded-2xl bg-paper-raised px-8 py-12 shadow-sm ring-1 ring-hairline">
      <h2 className="font-display text-section text-ink">{title}</h2>
      <p className="mt-3 font-body text-body text-ink-muted">{body}</p>
      {onRetry ? (
        <Button className="mt-6" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </div>
  );
}
