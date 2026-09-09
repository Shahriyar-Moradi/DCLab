"use client";

import { Badge } from "@/app/components/ui/Badge";
import { GlassPanel } from "@/app/components/ui/GlassPanel";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import { TabPanel, Tabs } from "@/app/components/ui/Tabs";
import type { BusinessWorkspaceDetail, PlatformBusinessDetail, PlatformDomain } from "@/lib/domain";
import Link from "next/link";
import { useState } from "react";
import { CapabilityNotice } from "./CapabilityNotice";
import { FilteredCollection } from "./FilteredCollection";
import { ExplorerMetrics } from "./ObjectFacts";
import { factsOf, fact, formatWhen, isWorkspaceDetail, nonempty } from "./helpers";
import { domainHref, explorerRoot } from "./paths";
import { MembershipTable, ModelTable, RunTable, WorkflowTable } from "./tables";

function DomainCards({
  domains,
  businessId,
  businessMode,
}: {
  domains: PlatformDomain[];
  businessId: string;
  businessMode: boolean;
}) {
  if (domains.length === 0) {
    return <p className="text-body text-ink-muted">No domains on this workspace.</p>;
  }
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      {domains.map((domain) => (
        <Link
          key={domain.id}
          href={domainHref(businessId, domain.id, businessMode)}
          className="rounded-xl border border-hairline bg-paper-raised p-5 transition-ui hover:border-navy/30"
        >
          <div className="flex min-w-0 items-start justify-between gap-3">
            <h2 className="min-w-0 break-words font-sans text-section text-ink">{domain.name}</h2>
            <StatusBadge status={domain.enabled ? "Enabled" : "Disabled"} tone={domain.enabled ? "green" : "amber"} />
          </div>
          {nonempty(domain.description) ? (
            <p className="mt-2 text-body text-ink-muted">{domain.description}</p>
          ) : null}
          <p className="mt-4 font-mono text-data text-ink-muted">
            {domain.workflow_count} workflows · {domain.run_count} runs
          </p>
        </Link>
      ))}
    </div>
  );
}

export function WorkspaceExplorer({
  business,
  businessMode,
}: {
  business: PlatformBusinessDetail | BusinessWorkspaceDetail;
  businessMode: boolean;
}) {
  const root = explorerRoot(businessMode);
  const hideModels = businessMode && isWorkspaceDetail(business) && business.capabilities.model_management !== true;
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "workflows", label: "Workflows" },
    { id: "models", label: "Models", disabled: hideModels },
    { id: "runs", label: "Runs" },
    ...(!businessMode ? [{ id: "memberships", label: "Memberships" }] : []),
  ].filter((tab) => !tab.disabled);
  const [tab, setTab] = useState("overview");
  const description = factsOf([
    fact("slug", business.slug, true),
    fact("legal", business.legal_name),
    fact("industry", business.industry),
  ])
    .map((item) => item.value)
    .join(" · ");

  return (
    <div>
      <PageHeader
        eyebrow={businessMode ? "Business workspace" : "Platform explorer"}
        breadcrumbs={[{ label: root.label, href: root.href }, { label: business.name }]}
        title={business.name}
        identifier={business.id}
        description={nonempty(description)}
      />
      <Tabs className="mt-6" items={tabs} value={tab} onChange={setTab} />

      <TabPanel id="overview" value={tab} className="mt-6 space-y-5">
        <ExplorerMetrics
          items={[
            { label: "Domains", value: String(business.domain_count) },
            { label: "Workflows", value: String(business.workflow_count) },
            { label: "Runs", value: String(business.run_count) },
            { label: "Pipelines", value: String(business.pipeline_count) },
            { label: "Models", value: String(business.model_count) },
            { label: "Members", value: String(business.membership_count) },
          ]}
        />
        {formatWhen(business.created_at) ? (
          <p className="font-mono text-data text-ink-muted">Created {formatWhen(business.created_at)}</p>
        ) : null}
        {businessMode && isWorkspaceDetail(business) ? (
          <GlassPanel title="Technical capabilities">
            <div className="flex flex-wrap gap-2">
              {Object.entries(business.capabilities).map(([key, enabled]) => (
                <Badge key={key} tone={enabled ? "green" : "amber"}>
                  {key}: {enabled ? "enabled" : "disabled"}
                </Badge>
              ))}
            </div>
            {!business.can_write ? (
              <p className="mt-4 text-body text-ink-muted">
                Read-only business access. Side-effecting actions are disabled and rejected by the API.
              </p>
            ) : null}
          </GlassPanel>
        ) : null}
        {hideModels ? <CapabilityNotice name="model_management" /> : null}
        <FilteredCollection
          rows={business.domains}
          haystack={(domain) => [domain.name, domain.slug, domain.description, domain.enabled ? "enabled" : "disabled"]}
          empty={<p className="text-body text-ink-muted">No domains on this workspace.</p>}
        >
          {(domains) => <DomainCards domains={domains} businessId={business.id} businessMode={businessMode} />}
        </FilteredCollection>
      </TabPanel>

      <TabPanel id="workflows" value={tab} className="mt-6">
        <FilteredCollection
          rows={business.workflows}
          haystack={(row) => [row.name, row.domain_name, row.status, row.business_objective, row.description]}
          empty={<WorkflowTable rows={[]} businessId={business.id} businessMode={businessMode} />}
        >
          {(rows) => <WorkflowTable rows={rows} businessId={business.id} businessMode={businessMode} />}
        </FilteredCollection>
      </TabPanel>

      {!hideModels ? (
        <TabPanel id="models" value={tab} className="mt-6">
          <FilteredCollection
            rows={business.models}
            haystack={(row) => [row.name, row.workflow_name, row.status, row.slug]}
            empty={<ModelTable rows={[]} businessId={business.id} businessMode={businessMode} />}
          >
            {(rows) => <ModelTable rows={rows} businessId={business.id} businessMode={businessMode} />}
          </FilteredCollection>
        </TabPanel>
      ) : null}

      <TabPanel id="runs" value={tab} className="mt-6">
        <FilteredCollection
          rows={business.runs}
          haystack={(row) => [row.id, row.workflow_name, row.domain_name, row.status]}
          empty={<RunTable rows={[]} businessId={business.id} businessMode={businessMode} showDomain showWorkflow />}
        >
          {(rows) => (
            <RunTable rows={rows} businessId={business.id} businessMode={businessMode} showDomain showWorkflow />
          )}
        </FilteredCollection>
      </TabPanel>

      {!businessMode ? (
        <TabPanel id="memberships" value={tab} className="mt-6">
          <FilteredCollection
            rows={business.memberships}
            haystack={(row) => [row.email, row.full_name, row.role, row.is_active ? "active" : "disabled"]}
            empty={<MembershipTable rows={[]} />}
          >
            {(rows) => <MembershipTable rows={rows} />}
          </FilteredCollection>
        </TabPanel>
      ) : null}
    </div>
  );
}
