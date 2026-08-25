"use client";

import Link from "next/link";
import { Badge } from "@/app/components/ui/Badge";
import { ConfidenceBar } from "@/app/components/ui/ConfidenceBar";
import { cn } from "@/lib/cn";
import { actionLabel, actionTone, confidenceTone, formatMoney, formatPercent, formatTimestamp, type DecisionView } from "@/lib/domain";

export function DecisionLedgerEntry({
  decision,
  variant = "full",
  className,
  animate,
}: {
  decision: DecisionView;
  variant?: "full" | "compact";
  className?: string;
  animate?: boolean;
}) {
  const tone = actionTone(decision.recommendedAction);
  const confTone = confidenceTone(decision.confidence);
  const compact = variant === "compact";

  return (
    <article
      className={cn(
        "rounded-2xl bg-paper-raised p-6 shadow-sm ring-1 ring-hairline",
        animate && "animate-fade-up",
        className,
      )}
    >
      <p className="font-mono text-data uppercase tracking-[0.06em] text-ink-muted">
        Decision · {decision.opportunityExternalId}
      </p>
      <div className="mt-4">
        <Badge tone={tone}>{actionLabel(decision.recommendedAction)}</Badge>
      </div>
      <p className="mt-6 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Expected value</p>
      <p className={cn("mt-1 font-mono text-ink", compact ? "text-data font-medium" : "text-title")}>
        {formatMoney(decision.expectedRevenue)}
      </p>
      {decision.conversionProbability != null ? (
        <p className="mt-1 font-mono text-data text-ink-muted">P(convert) {formatPercent(decision.conversionProbability)}</p>
      ) : null}
      <div className="mt-4">
        <p className="mb-2 font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">Confidence</p>
        <ConfidenceBar value={decision.confidence} tone={confTone} />
      </div>
      {!compact ? (
        <ul className="mt-6">
          {decision.reasoning.map((line) => (
            <li key={line} className="border-t border-hairline py-3 font-body text-body text-ink">
              {line}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-4 font-body text-body text-ink-muted">{decision.reasoning[0]}</p>
      )}
      <div className="mt-4 border-t border-hairline pt-3 font-mono text-data text-ink-muted">
        <p>
          {decision.modelVersion} · {decision.policyVersion}
        </p>
        {decision.createdAt ? <p>Generated {formatTimestamp(decision.createdAt)}</p> : null}
        {decision.id ? (
          <p className="mt-2">
            <Link className="text-brand underline-offset-2 hover:underline" href={`/decisions/${decision.id}`}>
              Open full decision
            </Link>
          </p>
        ) : null}
      </div>
    </article>
  );
}
