import { Fact, FactGrid } from "@/app/components/ui/Card";
import { MetricCard } from "@/app/components/ui/MetricCard";
import type { ExplorerFact } from "./helpers";

export function ObjectFacts({ facts }: { facts: ExplorerFact[] }) {
  if (facts.length === 0) return null;
  return (
    <FactGrid>
      {facts.map((fact) => (
        <Fact key={fact.label} label={fact.label} value={fact.value} mono={fact.mono} />
      ))}
    </FactGrid>
  );
}

export function ExplorerMetrics({ items }: { items: Array<{ label: string; value: string } | null | undefined> }) {
  const present = items.filter((item): item is { label: string; value: string } => Boolean(item));
  if (present.length === 0) return null;
  const columns =
    present.length >= 6 ? "lg:grid-cols-6" : present.length >= 4 ? "lg:grid-cols-4" : present.length === 3 ? "lg:grid-cols-3" : "lg:grid-cols-2";
  return (
    <div className={`grid gap-3 sm:grid-cols-2 ${columns}`}>
      {present.map((item) => (
        <MetricCard key={item.label} label={item.label} value={item.value} />
      ))}
    </div>
  );
}
