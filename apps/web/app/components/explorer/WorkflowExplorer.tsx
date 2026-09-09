"use client";

import { PageHeader } from "@/app/components/ui/PageHeader";
import { Panel } from "@/app/components/ui/Card";
import { TabPanel, Tabs } from "@/app/components/ui/Tabs";
import { statusTone } from "@/app/components/ui/StatusBadge";
import type { PlatformWorkflowDetail } from "@/lib/domain";
import { useMemo, useState } from "react";
import { ExplorerMetrics, ObjectFacts } from "./ObjectFacts";
import { factsOf, fact, formatWhen, nonempty, recordHasKeys } from "./helpers";
import { domainHref, explorerBase, explorerRoot } from "./paths";
import { FilteredCollection } from "./FilteredCollection";
import { ModelTable, RunTable } from "./tables";

export function WorkflowExplorer({
  workflow,
  businessId,
  businessMode,
}: {
  workflow: PlatformWorkflowDetail;
  businessId: string;
  businessMode: boolean;
}) {
  const root = explorerRoot(businessMode);
  const base = explorerBase(businessId, businessMode);
  const configFacts = recordHasKeys(workflow.config)
    ? factsOf(
        Object.entries(workflow.config).map(([key, value]) =>
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
  const tabs = useMemo(() => {
    const items = [
      { id: "overview", label: "Overview" },
      { id: "runs", label: "Runs" },
    ];
    if (workflow.models.length > 0) items.push({ id: "models", label: "Models" });
    if (configFacts.length > 0) items.push({ id: "settings", label: "Settings" });
    return items;
  }, [configFacts.length, workflow.models.length]);
  const [tab, setTab] = useState("overview");

  return (
    <div>
      <PageHeader
        eyebrow="Workflow"
        breadcrumbs={[
          { label: root.label, href: root.href },
          { label: workflow.business_name, href: base },
          { label: workflow.domain_name, href: domainHref(businessId, workflow.workspace_domain_id, businessMode) },
          { label: workflow.name },
        ]}
        title={workflow.name}
        identifier={workflow.id}
        description={nonempty(workflow.business_objective) ?? nonempty(workflow.description)}
        status={{ label: workflow.status, tone: statusTone(workflow.status) }}
      />
      <Tabs className="mt-6" items={tabs} value={tab} onChange={setTab} />
      <TabPanel id="overview" value={tab} className="mt-6 space-y-5">
        <ExplorerMetrics
          items={[
            { label: "Domain", value: workflow.domain_name },
            { label: "Status", value: workflow.status },
            { label: "Runs", value: String(workflow.run_count) },
            { label: "Models", value: String(workflow.model_count) },
          ]}
        />
        <Panel title="Context">
          <ObjectFacts
            facts={factsOf([
              fact("Slug", workflow.slug, true),
              fact("Domain", workflow.domain_name),
              fact("Description", workflow.description),
              fact("Created", formatWhen(workflow.created_at), true),
              fact("Updated", formatWhen(workflow.updated_at), true),
            ])}
          />
        </Panel>
      </TabPanel>
      <TabPanel id="runs" value={tab} className="mt-6">
        <FilteredCollection
          rows={workflow.runs}
          haystack={(row) => [row.id, row.status, row.workflow_name]}
          empty={<RunTable rows={[]} businessId={businessId} businessMode={businessMode} showWorkflow={false} />}
        >
          {(rows) => <RunTable rows={rows} businessId={businessId} businessMode={businessMode} showWorkflow={false} />}
        </FilteredCollection>
      </TabPanel>
      {workflow.models.length > 0 ? (
        <TabPanel id="models" value={tab} className="mt-6">
          <FilteredCollection
            rows={workflow.models}
            haystack={(row) => [row.name, row.status, row.slug]}
            empty={<ModelTable rows={[]} businessId={businessId} businessMode={businessMode} />}
          >
            {(rows) => <ModelTable rows={rows} businessId={businessId} businessMode={businessMode} />}
          </FilteredCollection>
        </TabPanel>
      ) : null}
      {configFacts.length > 0 ? (
        <TabPanel id="settings" value={tab} className="mt-6">
          <Panel title="Settings">
            <ObjectFacts facts={configFacts} />
          </Panel>
        </TabPanel>
      ) : null}
    </div>
  );
}
