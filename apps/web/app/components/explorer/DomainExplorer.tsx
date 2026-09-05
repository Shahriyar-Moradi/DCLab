"use client";

import { PageHeader } from "@/app/components/ui/PageHeader";
import { Panel } from "@/app/components/ui/Card";
import { TabPanel, Tabs } from "@/app/components/ui/Tabs";
import type { PlatformDomainDetail } from "@/lib/domain";
import { useState } from "react";
import { ExplorerMetrics, ObjectFacts } from "./ObjectFacts";
import { factsOf, nonempty, recordHasKeys } from "./helpers";
import { explorerBase, explorerRoot } from "./paths";
import { FilteredCollection } from "./FilteredCollection";
import { RunTable, WorkflowTable } from "./tables";

export function DomainExplorer({
  domain,
  businessId,
  businessMode,
}: {
  domain: PlatformDomainDetail;
  businessId: string;
  businessMode: boolean;
}) {
  const root = explorerRoot(businessMode);
  const base = explorerBase(businessId, businessMode);
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "workflows", label: "Workflows" },
    { id: "runs", label: "Runs" },
  ];
  const [tab, setTab] = useState("overview");
  const configFacts = recordHasKeys(domain.config)
    ? factsOf(
        Object.entries(domain.config).map(([key, value]) =>
          value == null || value === ""
            ? null
            : {
                label: key,
                value: typeof value === "string" ? value : JSON.stringify(value),
                mono: typeof value !== "string",
              },
        ),
      )
    : [];

  return (
    <div>
      <PageHeader
        eyebrow="Domain"
        breadcrumbs={[
          { label: root.label, href: root.href },
          { label: domain.business_name, href: base },
          { label: domain.name },
        ]}
        title={domain.name}
        identifier={domain.id}
        description={nonempty(domain.description)}
        status={{
          label: domain.enabled ? "Enabled" : "Disabled",
          tone: domain.enabled ? "green" : "amber",
        }}
      />
      <Tabs className="mt-6" items={tabs} value={tab} onChange={setTab} />
      <TabPanel id="overview" value={tab} className="mt-6 space-y-5">
        <ExplorerMetrics
          items={[
            { label: "Workflows", value: String(domain.workflow_count) },
            { label: "Runs", value: String(domain.run_count) },
          ]}
        />
        <Panel title="Context">
          <ObjectFacts
            facts={factsOf([
              { label: "Slug", value: domain.slug, mono: true },
              { label: "Business", value: domain.business_name },
            ])}
          />
        </Panel>
        {configFacts.length > 0 ? (
          <Panel title="Settings">
            <ObjectFacts facts={configFacts} />
          </Panel>
        ) : null}
      </TabPanel>
      <TabPanel id="workflows" value={tab} className="mt-6">
        <FilteredCollection
          rows={domain.workflows}
          haystack={(row) => [row.name, row.status, row.business_objective, row.description]}
          empty={<WorkflowTable rows={[]} businessId={businessId} businessMode={businessMode} showDomain={false} />}
        >
          {(rows) => (
            <WorkflowTable rows={rows} businessId={businessId} businessMode={businessMode} showDomain={false} />
          )}
        </FilteredCollection>
      </TabPanel>
      <TabPanel id="runs" value={tab} className="mt-6">
        <FilteredCollection
          rows={domain.runs}
          haystack={(row) => [row.id, row.workflow_name, row.status]}
          empty={<RunTable rows={[]} businessId={businessId} businessMode={businessMode} showWorkflow />}
        >
          {(rows) => <RunTable rows={rows} businessId={businessId} businessMode={businessMode} showWorkflow />}
        </FilteredCollection>
      </TabPanel>
    </div>
  );
}
