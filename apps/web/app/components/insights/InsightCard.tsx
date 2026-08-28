"use client";

import { Badge } from "@/app/components/ui/Badge";
import { formatMoney, toneFromConfidenceBand, type ClientInsight } from "@/lib/domain";

export function InsightCard({ insight }: { insight: ClientInsight }) {
  const tone = toneFromConfidenceBand(insight.confidence_band);
  return (
    <article className="rounded bg-paper-raised p-6">
      <p className="font-mono text-data uppercase tracking-[0.06em] text-ink-muted">{insight.subject_id}</p>
      <h3 className="mt-3 font-display text-lg text-ink">{insight.headline}</h3>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <Badge tone={tone}>{insight.confidence_band} confidence</Badge>
        <span className="font-mono text-data text-ink-muted">
          {formatMoney(insight.expected_value, insight.currency)}
        </span>
      </div>
      <p className="mt-5 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Recommended action</p>
      <p className="mt-1 font-body text-body text-ink">{insight.recommended_action}</p>
      {insight.reasoning.length > 0 ? (
        <ul className="mt-4">
          {insight.reasoning.map((line) => (
            <li key={line} className="border-t border-hairline py-3 font-body text-body text-ink-muted">
              {line}
            </li>
          ))}
        </ul>
      ) : null}
    </article>
  );
}
