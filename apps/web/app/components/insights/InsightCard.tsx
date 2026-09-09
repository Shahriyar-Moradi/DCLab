"use client";

import { Badge } from "@/app/components/ui/Badge";
import { Card, Fact, FactGrid } from "@/app/components/ui/Card";
import { formatMoney, formatTimestamp, toneFromConfidenceBand, type ClientInsight } from "@/lib/domain";

export function InsightCard({ insight }: { insight: ClientInsight }) {
  const tone = toneFromConfidenceBand(insight.confidence_band);
  return (
    <article>
      <Card className="p-5">
        <p className="font-mono text-data text-ink-muted">{insight.subject_id}</p>
        <h3 className="mt-2 font-sans text-section text-ink">{insight.headline}</h3>
        <div className="mt-3">
          <Badge tone={tone} emphasis="soft">
            {insight.confidence_band} confidence
          </Badge>
        </div>
        <FactGrid className="mt-5 xl:grid-cols-2">
          <Fact label="Expected value" value={formatMoney(insight.expected_value, insight.currency)} mono />
          <Fact label="Generated" value={formatTimestamp(insight.generated_at) || "—"} mono />
        </FactGrid>
        {insight.recommended_action ? (
          <div className="mt-5">
            <p className="product-eyebrow">Next action</p>
            <p className="mt-1 text-body text-ink">{insight.recommended_action}</p>
          </div>
        ) : null}
        {insight.reasoning.length > 0 ? (
          <ul className="mt-5">
            {insight.reasoning.map((line) => (
              <li key={line} className="border-t border-hairline py-3 text-body text-ink-muted">
                {line}
              </li>
            ))}
          </ul>
        ) : null}
      </Card>
    </article>
  );
}
