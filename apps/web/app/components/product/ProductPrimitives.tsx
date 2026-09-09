import { cn } from "@/lib/cn";

export function RunProgress({
  items,
}: {
  items: Array<{ id: string; label: string; state: "done" | "current" | "upcoming" }>;
}) {
  return (
    <ol className="product-run-progress">
      {items.map((item) => (
        <li
          key={item.id}
          className={cn("product-run-step", `product-run-step-${item.state}`)}
          aria-current={item.state === "current" ? "step" : undefined}
        >
          <span className="product-run-step-marker" aria-hidden />
          <span>{item.label}</span>
        </li>
      ))}
    </ol>
  );
}
