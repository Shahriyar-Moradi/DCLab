"use client";

import { DecisionLedgerEntry } from "@/app/components/decisions/DecisionLedgerEntry";
import { Button } from "@/app/components/ui/Button";
import { Card, Fact, FactGrid, Panel } from "@/app/components/ui/Card";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { useDecisions, useGenerateDecision, useOpportunity } from "@/lib/application";
import { decisionToView, formatMoney, formatTimestamp, generateToView, type SignalTone } from "@/lib/domain";
import { useParams } from "next/navigation";
import { useState } from "react";

function stageTone(stage: string): SignalTone {
  if (stage === "closed_won") return "green";
  if (stage === "closed_lost") return "oxblood";
  return "amber";
}

export default function OpportunityDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;
  const opportunity = useOpportunity(id);
  const existing = useDecisions({ opportunity_id: id, limit: 1 });
  const generate = useGenerateDecision();
  const [fresh, setFresh] = useState(false);

  if (opportunity.isPending) {
    return (
      <div>
        <PageHeader
          breadcrumbs={[
            { label: "Opportunities", href: "/app/opportunities" },
            { label: id },
          ]}
          title="Opportunity"
        />
        <Skeleton className="h-80" />
      </div>
    );
  }
  if (opportunity.isError || !opportunity.data) {
    return (
      <div>
        <PageHeader
          breadcrumbs={[{ label: "Opportunities", href: "/app/opportunities" }, { label: id }]}
          title="Opportunity"
        />
        <ErrorState
          title="Opportunity not found"
          body="That ID is not in the database. Check the opportunities list."
          onRetry={() => void opportunity.refetch()}
        />
      </div>
    );
  }
  const row = opportunity.data;
  const current = generate.data
    ? generateToView(generate.data)
    : existing.data?.items[0]
      ? decisionToView(existing.data.items[0])
      : null;
  const contextFacts = [
    row.close_date ? { label: "Close date", value: formatTimestamp(row.close_date) || row.close_date, mono: true } : null,
    row.last_contact_days_ago != null
      ? { label: "Last contact (days ago)", value: String(row.last_contact_days_ago), mono: true }
      : null,
    row.engagement_score != null
      ? { label: "Engagement score", value: String(row.engagement_score), mono: true }
      : null,
    row.sales_rep_available != null
      ? { label: "Sales rep available", value: row.sales_rep_available ? "Yes" : "No" }
      : null,
    row.industry ? { label: "Industry", value: row.industry } : null,
    row.num_interactions != null
      ? { label: "Interactions", value: String(row.num_interactions), mono: true }
      : null,
    row.converted != null ? { label: "Converted", value: String(row.converted), mono: true } : null,
  ].filter((item): item is { label: string; value: string; mono?: boolean } => item != null);

  return (
    <div>
      <PageHeader
        eyebrow="Opportunity"
        title={row.external_id}
        description={`Customer ${row.customer_id}`}
        breadcrumbs={[
          { label: "Opportunities", href: "/app/opportunities" },
          { label: row.external_id },
        ]}
        status={{ label: row.stage.replaceAll("_", " "), tone: stageTone(row.stage) }}
        actions={
          <Button
            variant={current ? "secondary" : "primary"}
            loading={generate.isPending}
            onClick={() =>
              generate.mutate(row.external_id, {
                onSuccess: () => setFresh(true),
              })
            }
          >
            {generate.isPending ? "Scoring…" : current ? "Regenerate" : "Generate decision"}
          </Button>
        }
      />

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="Commercial">
          <FactGrid className="xl:grid-cols-2">
            <Fact label="Amount" value={formatMoney(row.amount, row.currency)} mono />
            <Fact label="Currency" value={row.currency} mono />
            <Fact label="Stage" value={row.stage.replaceAll("_", " ")} />
            <Fact label="Source" value={row.source} />
          </FactGrid>
        </Panel>
        <Panel title="Record">
          <FactGrid className="xl:grid-cols-2">
            <Fact label="Customer" value={row.customer_id} mono />
            <Fact label="Owner" value={row.owner_id} mono />
            <Fact label="Created" value={formatTimestamp(row.created_at) || "—"} mono />
            <Fact label="Org" value={row.org_id} mono />
          </FactGrid>
        </Panel>
      </div>

      {contextFacts.length ? (
        <Panel className="mt-5" title="Context">
          <FactGrid>
            {contextFacts.map((item) => (
              <Fact key={item.label} label={item.label} value={item.value} mono={item.mono} />
            ))}
          </FactGrid>
        </Panel>
      ) : null}

      <Panel className="mt-5" title="Decision" description="Score this opportunity and record a recommended action.">
        {current ? (
          <DecisionLedgerEntry decision={current} variant="compact" animate={fresh} />
        ) : (
          <Card className="border-dashed px-6 py-8">
            <p className="text-body text-ink-muted">No decision yet for this opportunity.</p>
          </Card>
        )}
        {generate.isError ? (
          <p className="mt-3 text-body text-oxblood" role="alert">
            {generate.error.message}
          </p>
        ) : null}
      </Panel>
    </div>
  );
}
