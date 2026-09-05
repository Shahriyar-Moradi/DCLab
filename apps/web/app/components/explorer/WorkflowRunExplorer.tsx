import { Panel } from "@/app/components/ui/Card";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { statusTone } from "@/app/components/ui/StatusBadge";
import type { BusinessWorkflowRunDetail, PlatformWorkflowRunDetail } from "@/lib/domain";
import { ExplorerMetrics, ObjectFacts } from "./ObjectFacts";
import { canOpenMonitor, fact, factsOf, formatWhen, isBusinessRun, nonempty } from "./helpers";
import { domainHref, explorerBase, explorerRoot, workflowHref } from "./paths";
import { FilteredCollection } from "./FilteredCollection";
import { PipelineTable } from "./tables";

export function WorkflowRunExplorer({
  run,
  businessId,
  businessMode,
}: {
  run: PlatformWorkflowRunDetail | BusinessWorkflowRunDetail;
  businessId: string;
  businessMode: boolean;
}) {
  const root = explorerRoot(businessMode);
  const base = explorerBase(businessId, businessMode);
  const canMonitor = canOpenMonitor(businessMode, isBusinessRun(run) ? run.capabilities : undefined);
  const failedPipelines = run.pipelines.filter((pipeline) => nonempty(pipeline.failure_reason));

  return (
    <div>
      <PageHeader
        eyebrow="Workflow run"
        breadcrumbs={[
          { label: root.label, href: root.href },
          { label: run.business_name, href: base },
          { label: run.domain_name, href: domainHref(businessId, run.workspace_domain_id, businessMode) },
          { label: run.workflow_name, href: workflowHref(businessId, run.workflow_id, businessMode) },
          { label: "Workflow run" },
        ]}
        title={run.workflow_name}
        identifier={run.id}
        status={{ label: run.status, tone: statusTone(run.status) }}
      />

      <div className="space-y-5">
        <ExplorerMetrics
          items={[
            fact("Source", nonempty(run.source_filename) ?? run.source_type),
            fact("Target", nonempty(run.resolved_target) ?? nonempty(run.explicit_target)),
            fact("Task", run.task_type),
            { label: "Pipelines", value: String(run.pipeline_count) },
          ]}
        />

        <Panel title="Summary">
          <ObjectFacts
            facts={factsOf([
              fact("Trigger", run.trigger_type),
              fact("Source type", run.source_type, true),
              fact("Source upload", run.source_upload_id, true),
              fact("File", run.source_filename),
              fact("Explicit target", run.explicit_target, true),
              fact("Resolved target", run.resolved_target, true),
              fact("Task", run.task_type),
              { label: "Model versions", value: String(run.model_version_count), mono: true },
              fact("Started", formatWhen(run.started_at), true),
              fact("Completed", formatWhen(run.completed_at), true),
              fact("Created", formatWhen(run.created_at), true),
            ])}
          />
        </Panel>

        {nonempty(run.failure_reason) ? (
          <Panel title="Errors">
            <p className="text-body text-oxblood" role="alert">
              {run.failure_reason}
            </p>
          </Panel>
        ) : null}

        {failedPipelines.length > 0 ? (
          <Panel title="Pipeline errors">
            <ul className="space-y-2">
              {failedPipelines.map((pipeline) => (
                <li key={pipeline.id} className="text-body text-oxblood">
                  <span className="font-mono text-data">{pipeline.pipeline_name}</span>
                  {": "}
                  {pipeline.failure_reason}
                </li>
              ))}
            </ul>
          </Panel>
        ) : null}

        <Panel
          title="Pipeline runs"
          description="Each pipeline is an independent technical run. All pipelines are rendered; none are collapsed into the workflow invocation."
        >
          <FilteredCollection
            rows={run.pipelines}
            haystack={(row) => [row.pipeline_name, row.pipeline_purpose, row.status, row.dataset_name, row.model_name]}
            empty={<PipelineTable rows={[]} businessId={businessId} businessMode={businessMode} canMonitor={canMonitor} />}
          >
            {(rows) => (
              <PipelineTable rows={rows} businessId={businessId} businessMode={businessMode} canMonitor={canMonitor} />
            )}
          </FilteredCollection>
        </Panel>
      </div>
    </div>
  );
}
