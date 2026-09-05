import { DataTable, type DataTableColumn } from "@/app/components/ui/DataTable";
import { StatusBadge } from "@/app/components/ui/StatusBadge";
import type {
  PlatformMembership,
  PlatformModel,
  PlatformModelVersion,
  PlatformPipeline,
  PlatformWorkflow,
  PlatformWorkflowRun,
} from "@/lib/domain";
import Link from "next/link";
import { formatMetricMap, formatWhen, nonempty, recordHasKeys } from "./helpers";
import { modelHref, monitorHref, workflowHref, workflowRunHref } from "./paths";

const LINK = "block max-w-[16rem] break-words font-semibold text-navy hover:underline";
const MONO_LINK = "block max-w-[16rem] break-all font-mono text-data text-navy hover:underline";

export function WorkflowTable({
  rows,
  businessId,
  businessMode,
  showDomain = true,
}: {
  rows: PlatformWorkflow[];
  businessId: string;
  businessMode: boolean;
  showDomain?: boolean;
}) {
  if (rows.length === 0) {
    return <p className="text-body text-ink-muted">No workflows on this object.</p>;
  }
  const columns: DataTableColumn<PlatformWorkflow>[] = [
    {
      id: "workflow",
      header: "Workflow",
      cell: (row) => (
        <Link className={LINK} href={workflowHref(businessId, row.id, businessMode)}>
          {row.name}
        </Link>
      ),
    },
  ];
  if (showDomain) {
    columns.push({ id: "domain", header: "Domain", cell: (row) => row.domain_name });
  }
  columns.push(
    {
      id: "objective",
      header: "Objective",
      cell: (row) => nonempty(row.business_objective) ?? nonempty(row.description) ?? "—",
    },
    {
      id: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} />,
    },
    { id: "runs", header: "Runs", mono: true, cell: (row) => String(row.run_count) },
    { id: "models", header: "Models", mono: true, cell: (row) => String(row.model_count) },
  );
  return <DataTable columns={columns} rows={rows} rowKey={(row) => row.id} />;
}

export function RunTable({
  rows,
  businessId,
  businessMode,
  showDomain = false,
  showWorkflow = true,
}: {
  rows: PlatformWorkflowRun[];
  businessId: string;
  businessMode: boolean;
  showDomain?: boolean;
  showWorkflow?: boolean;
}) {
  if (rows.length === 0) {
    return <p className="text-body text-ink-muted">No workflow runs on this object.</p>;
  }
  const columns: DataTableColumn<PlatformWorkflowRun>[] = [
    {
      id: "run",
      header: "Workflow run",
      cell: (row) => (
        <div>
          {showWorkflow ? (
            <Link className={LINK} href={workflowRunHref(businessId, row.id, businessMode)}>
              {row.workflow_name}
            </Link>
          ) : (
            <Link className={MONO_LINK} href={workflowRunHref(businessId, row.id, businessMode)}>
              {row.id}
            </Link>
          )}
          {showWorkflow ? <p className="font-mono text-data text-ink-muted">{row.id}</p> : null}
        </div>
      ),
    },
  ];
  if (showDomain) {
    columns.push({ id: "domain", header: "Domain", cell: (row) => row.domain_name });
  }
  columns.push(
    {
      id: "source",
      header: "Source",
      cell: (row) => nonempty(row.source_filename) ?? row.source_type,
    },
    {
      id: "target",
      header: "Target / task",
      cell: (row) =>
        `${nonempty(row.resolved_target) ?? nonempty(row.explicit_target) ?? "—"} · ${nonempty(row.task_type) ?? "—"}`,
    },
    {
      id: "status",
      header: "Status",
      cell: (row) => <StatusBadge status={row.status} />,
    },
    { id: "pipelines", header: "Pipelines", mono: true, cell: (row) => String(row.pipeline_count) },
    {
      id: "created",
      header: "Created",
      mono: true,
      cell: (row) => formatWhen(row.created_at) ?? row.created_at,
    },
  );
  return <DataTable columns={columns} rows={rows} rowKey={(row) => row.id} />;
}

export function ModelTable({
  rows,
  businessId,
  businessMode,
}: {
  rows: PlatformModel[];
  businessId: string;
  businessMode: boolean;
}) {
  if (rows.length === 0) {
    return <p className="text-body text-ink-muted">No models on this object.</p>;
  }
  return (
    <DataTable
      columns={[
        {
          id: "model",
          header: "Model",
          cell: (row) => (
            <Link className={LINK} href={modelHref(businessId, row.id, businessMode)}>
              {row.name}
            </Link>
          ),
        },
        { id: "workflow", header: "Workflow", cell: (row) => <span className="break-words">{row.workflow_name}</span> },
        {
          id: "status",
          header: "Status",
          cell: (row) => <StatusBadge status={row.status} />,
        },
        { id: "versions", header: "Versions", mono: true, cell: (row) => String(row.versions.length) },
      ]}
      rows={rows}
      rowKey={(row) => row.id}
    />
  );
}

export function MembershipTable({ rows }: { rows: PlatformMembership[] }) {
  if (rows.length === 0) {
    return <p className="text-body text-ink-muted">No memberships on this workspace.</p>;
  }
  return (
    <DataTable
      columns={[
        { id: "email", header: "User", mono: true, cell: (row) => row.email },
        { id: "name", header: "Name", cell: (row) => nonempty(row.full_name) ?? "—" },
        { id: "role", header: "Role", cell: (row) => row.role },
        { id: "status", header: "Status", cell: (row) => (row.is_active ? "Active" : "Disabled") },
      ]}
      rows={rows}
      rowKey={(row) => row.id}
    />
  );
}

export function PipelineTable({
  rows,
  businessId,
  businessMode,
  canMonitor,
}: {
  rows: PlatformPipeline[];
  businessId: string;
  businessMode: boolean;
  canMonitor: boolean;
}) {
  if (rows.length === 0) {
    return <p className="text-body text-ink-muted">No pipeline runs on this workflow run.</p>;
  }
  return (
    <DataTable
      columns={[
        { id: "index", header: "#", mono: true, cell: (row) => String(row.pipeline_index + 1) },
        {
          id: "pipeline",
          header: "Pipeline",
          cell: (row) => (
            <div>
              <span className="break-words font-semibold">{row.pipeline_name}</span>
              <p className="break-all font-mono text-data text-ink-muted">{row.id}</p>
            </div>
          ),
        },
        { id: "purpose", header: "Purpose", cell: (row) => row.pipeline_purpose },
        {
          id: "status",
          header: "Status",
          cell: (row) => <StatusBadge status={row.status} />,
        },
        { id: "candidates", header: "Candidates", mono: true, cell: (row) => String(row.candidate_count) },
        {
          id: "dataset",
          header: "Dataset",
          cell: (row) => row.dataset_name,
        },
        {
          id: "model",
          header: "Selected model",
          cell: (row) =>
            row.model_asset_id ? (
              <Link className={LINK} href={modelHref(businessId, row.model_asset_id, businessMode)}>
                {row.model_name} {row.model_version}
              </Link>
            ) : (
              "No successful version"
            ),
        },
        {
          id: "monitor",
          header: "Monitor",
          cell: (row) =>
            canMonitor ? (
              <Link className={LINK} href={monitorHref(row.id, businessId, businessMode)}>
                Open monitor
              </Link>
            ) : (
              <span className="text-ink-muted">Not enabled</span>
            ),
        },
      ]}
      rows={rows}
      rowKey={(row) => row.id}
    />
  );
}

export function ModelVersionTable({
  rows,
  businessId,
  businessMode,
  canMonitor,
}: {
  rows: PlatformModelVersion[];
  businessId: string;
  businessMode: boolean;
  canMonitor: boolean;
}) {
  if (rows.length === 0) {
    return <p className="text-body text-ink-muted">No selected versions on this model.</p>;
  }
  return (
    <DataTable
      columns={[
        { id: "version", header: "Version", mono: true, cell: (row) => row.version },
        {
          id: "candidate",
          header: "Selected candidate",
          mono: true,
          cell: (row) => row.selected_candidate_id,
        },
        {
          id: "run",
          header: "Workflow run",
          cell: (row) => (
            <Link className={MONO_LINK} href={workflowRunHref(businessId, row.workflow_run_id, businessMode)}>
              {row.workflow_run_id}
            </Link>
          ),
        },
        {
          id: "pipeline",
          header: "Pipeline",
          cell: (row) =>
            canMonitor ? (
              <Link className={LINK} href={monitorHref(row.pipeline_run_id, businessId, businessMode)}>
                Pipeline Monitor
              </Link>
            ) : (
              <span className="text-ink-muted">Not enabled</span>
            ),
        },
        {
          id: "metrics",
          header: "Evaluation",
          cell: (row) => (recordHasKeys(row.metrics) ? formatMetricMap(row.metrics) : "—"),
        },
        {
          id: "digest",
          header: "Artifact",
          mono: true,
          cell: (row) => row.content_digest,
        },
        {
          id: "created",
          header: "Created",
          mono: true,
          cell: (row) => formatWhen(row.created_at) ?? row.created_at,
        },
      ]}
      rows={rows}
      rowKey={(row) => row.id}
    />
  );
}
