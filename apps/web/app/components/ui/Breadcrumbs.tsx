import { cn } from "@/lib/cn";
import Link from "next/link";

export type BreadcrumbItem = {
  label: string;
  href?: string;
};

export function Breadcrumbs({ items, className }: { items: BreadcrumbItem[]; className?: string }) {
  return (
    <nav aria-label="Breadcrumb" className={cn("product-breadcrumbs", className)}>
      <ol className="m-0 flex list-none flex-wrap items-center gap-2 p-0">
        {items.map((item, index) => (
          <li key={`${item.label}-${index}`} className="inline-flex max-w-full min-w-0 items-center gap-2">
            {item.href ? (
              <Link href={item.href} className="min-w-0 break-words">
                {item.label}
              </Link>
            ) : (
              <span aria-current="page" className="min-w-0 break-words">
                {item.label}
              </span>
            )}
            {index < items.length - 1 ? <span aria-hidden>›</span> : null}
          </li>
        ))}
      </ol>
    </nav>
  );
}
