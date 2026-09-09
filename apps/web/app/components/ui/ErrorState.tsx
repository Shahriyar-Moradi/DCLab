import { Button } from "./Button";
import { Card } from "./Card";

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
    <Card role="alert" className="px-6 py-10 sm:px-8 sm:py-12">
      <h2 className="font-sans text-section text-ink">{title}</h2>
      <p className="mt-3 font-sans text-body text-ink-muted">{body}</p>
      {onRetry ? (
        <Button className="mt-6" variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      ) : null}
    </Card>
  );
}
