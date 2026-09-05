import { cn } from "@/lib/cn";

export { Breadcrumbs as ObjectBreadcrumbs, type BreadcrumbItem } from "@/app/components/ui/Breadcrumbs";
export { GlassPanel } from "@/app/components/ui/GlassPanel";
export { MetricCard } from "@/app/components/ui/MetricCard";
export { PageHeader as ProductPageHeader } from "@/app/components/ui/PageHeader";
export { StatusBadge } from "@/app/components/ui/StatusBadge";

export function RunProgress({
  items,
}: {
  items: Array<{ id: string; label: string; state: "done" | "current" | "upcoming" }>;
}) {
  return (
    <ol className="product-run-progress">
      {items.map((item) => (
        <li key={item.id} className={cn("product-run-step", `product-run-step-${item.state}`)}>
          <span className="product-run-step-marker" aria-hidden />
          <span>{item.label}</span>
        </li>
      ))}
    </ol>
  );
}
