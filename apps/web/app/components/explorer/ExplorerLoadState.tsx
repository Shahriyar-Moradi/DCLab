import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader, type BreadcrumbItem } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";

export function ExplorerLoadState({
  breadcrumbs,
  title,
  pending,
  error,
  errorBody,
  onRetry,
}: {
  breadcrumbs: BreadcrumbItem[];
  title: string;
  pending: boolean;
  error: boolean;
  errorBody: string;
  onRetry: () => void;
}) {
  if (pending) {
    return (
      <div>
        <PageHeader breadcrumbs={breadcrumbs} title={title} />
        <Skeleton className="h-80" />
      </div>
    );
  }
  if (error) {
    return (
      <div>
        <PageHeader breadcrumbs={breadcrumbs} title={title} />
        <ErrorState body={errorBody} onRetry={onRetry} />
      </div>
    );
  }
  return null;
}
