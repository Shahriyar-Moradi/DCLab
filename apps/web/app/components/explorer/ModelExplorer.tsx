import { Panel } from "@/app/components/ui/Card";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { statusTone } from "@/app/components/ui/StatusBadge";
import type { BusinessModelDetail, PlatformModelDetail } from "@/lib/domain";
import { ExplorerMetrics, ObjectFacts } from "./ObjectFacts";
import { canOpenMonitor, fact, factsOf, formatWhen, isBusinessModel, nonempty } from "./helpers";
import { explorerBase, explorerRoot, workflowHref } from "./paths";
import { FilteredCollection } from "./FilteredCollection";
import { ModelVersionTable } from "./tables";

export function ModelExplorer({
  model,
  businessId,
  businessMode,
}: {
  model: PlatformModelDetail | BusinessModelDetail;
  businessId: string;
  businessMode: boolean;
}) {
  const root = explorerRoot(businessMode);
  const base = explorerBase(businessId, businessMode);
  const canMonitor = canOpenMonitor(businessMode, isBusinessModel(model) ? model.capabilities : undefined);

  return (
    <div>
      <PageHeader
        eyebrow="Model"
        breadcrumbs={[
          { label: root.label, href: root.href },
          { label: model.business_name, href: base },
          { label: model.domain_name },
          { label: model.workflow_name, href: workflowHref(businessId, model.workflow_id, businessMode) },
          { label: model.name },
        ]}
        title={model.name}
        identifier={model.id}
        description={nonempty(model.description)}
        status={{ label: model.status, tone: statusTone(model.status) }}
      />

      <div className="space-y-5">
        <ExplorerMetrics
          items={[
            { label: "Status", value: model.status },
            { label: "Versions", value: String(model.versions.length) },
            { label: "Pipeline monitor", value: canMonitor ? "Available" : "Not enabled" },
          ]}
        />

        <Panel title="Context">
          <ObjectFacts
            facts={factsOf([
              fact("Slug", model.slug, true),
              fact("Workflow", model.workflow_name),
              fact("Domain", model.domain_name),
              fact("Created", formatWhen(model.created_at), true),
              fact("Updated", formatWhen(model.updated_at), true),
            ])}
          />
        </Panel>

        <Panel title="Immutable selected versions">
          <FilteredCollection
            rows={model.versions}
            haystack={(row) => [row.version, row.selected_candidate_id, row.workflow_run_id, row.content_digest]}
            empty={
              <ModelVersionTable
                rows={[]}
                businessId={businessId}
                businessMode={businessMode}
                canMonitor={canMonitor}
              />
            }
          >
            {(rows) => (
              <ModelVersionTable
                rows={rows}
                businessId={businessId}
                businessMode={businessMode}
                canMonitor={canMonitor}
              />
            )}
          </FilteredCollection>
        </Panel>
      </div>
    </div>
  );
}
