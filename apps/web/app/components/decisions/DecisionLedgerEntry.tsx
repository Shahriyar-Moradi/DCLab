"use client";

import Link from "next/link";
import { Badge } from "@/app/components/ui/Badge";
import { Card, Fact, FactGrid } from "@/app/components/ui/Card";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { cn } from "@/lib/cn";
import { actionLabel, actionTone, formatMoney, formatTimestamp, toneFromConfidenceBand, type DecisionView } from "@/lib/domain";

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
  const confTone = toneFromConfidenceBand(decision.confidenceBand);
  const compact = variant === "compact";

  return (
    <article className={cn(animate && "animate-fade-up", className)}>
      <Card className="p-5">
        <p className="font-mono text-data text-ink-muted">
          {decision.opportunityExternalId}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Badge tone={tone} emphasis="soft">
            {actionLabel(decision.recommendedAction)}
          </Badge>
          <Badge tone={confTone} emphasis="soft">
            {decision.confidenceBand} confidence
          </Badge>
          {decision.status ? <StatusBadge status={decision.status.replaceAll("_", " ")} /> : null}
        </div>
        <FactGrid className="mt-5 xl:grid-cols-2">
          <Fact label="Expected value" value={formatMoney(decision.expectedRevenue)} mono />
          {decision.createdAt ? (
            <Fact label="Generated" value={formatTimestamp(decision.createdAt) || "—"} mono />
          ) : null}
        </FactGrid>
        {!compact ? (
          <>
            {decision.reasoning.length ? (
              <ul className="mt-5">
                {decision.reasoning.map((line) => (
                  <li key={line} className="border-t border-hairline py-3 text-body text-ink">
                    {line}
                  </li>
                ))}
              </ul>
            ) : null}
            <div className="mt-5 border-t border-hairline pt-3 font-mono text-data text-ink-muted">
              <p>Policy {decision.policyVersion}</p>
            </div>
          </>
        ) : decision.reasoning[0] ? (
          <p className="mt-4 text-body text-ink-muted">{decision.reasoning[0]}</p>
        ) : null}
        {decision.id ? (
          <p className="mt-4">
            <Link className="font-medium text-navy underline-offset-2 hover:underline" href={`/app/decisions/${decision.id}`}>
              Open full decision
            </Link>
          </p>
        ) : null}
      </Card>
    </article>
  );
}
