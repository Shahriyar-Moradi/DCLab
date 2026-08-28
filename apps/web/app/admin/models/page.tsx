"use client";

import { Badge } from "@/app/components/ui/Badge";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useAdminModelRegistry } from "@/lib/application";
import type { RegisteredModel } from "@/lib/domain/schemas";
import Link from "next/link";

function metricEntries(metrics: Record<string, unknown>): [string, number][] {
  return Object.entries(metrics).filter(
    (entry): entry is [string, number] => typeof entry[1] === "number",
  );
}

function DetailLink({ row }: { row: RegisteredModel }) {
  if (row.source === "experiment") {
    return (
      <Link className="text-navy underline-offset-2 hover:underline" href={`/admin/lab/experiments/${row.id}`}>
        {row.id}
      </Link>
    );
  }
  if (row.source === "client_trial") {
    return (
      <Link className="text-navy underline-offset-2 hover:underline" href={`/admin/models/client-trials/${row.id}`}>
        {row.id}
      </Link>
    );
  }
  return <span className="font-mono text-data text-ink-muted">{row.id}</span>;
}

export default function ModelRegistryPage() {
  const query = useAdminModelRegistry();

  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError) {
    return <ErrorState body="Could not load the model registry." onRetry={() => void query.refetch()} />;
  }

  const rows = query.data ?? [];

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">DCLab Admin</p>
      <h1 className="mt-2 font-display text-title text-ink">Model registry</h1>
      <p className="mt-2 max-w-2xl font-body text-body text-ink-muted">
        Every model this system has trained — Lab experiments (client/uploaded data) and the bundled
        simulation pack. Full, unrestricted detail; nothing here goes through the translation layer.
      </p>
      <div className="mt-8">
        <Table>
          <thead>
            <tr>
              <Th>Source</Th>
              <Th>Name</Th>
              <Th>Status</Th>
              <Th>Model family</Th>
              <Th>Fusion</Th>
              <Th>Metrics</Th>
              <Th>Candidates</Th>
              <Th>Created</Th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.source}:${row.id}`}>
                <Td>
                  <Badge tone={row.source === "experiment" ? "green" : row.source === "simulation" ? "amber" : "oxblood"}>
                    {row.source}
                  </Badge>
                </Td>
                <Td>
                  {row.name}
                  <br />
                  <DetailLink row={row} />
                </Td>
                <Td>{row.status}</Td>
                <Td mono>{row.model_family ?? "—"}</Td>
                <Td mono>{row.fusion ?? "—"}</Td>
                <Td mono>
                  {metricEntries(row.metrics).length === 0
                    ? "—"
                    : metricEntries(row.metrics)
                        .map(([key, value]) => `${key}=${value.toFixed(3)}`)
                        .join(" · ")}
                </Td>
                <Td mono>{row.candidate_count ?? "—"}</Td>
                <Td mono>{new Date(row.created_at).toLocaleString()}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {rows.length === 0 ? (
          <p className="mt-6 font-body text-body text-ink-muted">No models trained yet.</p>
        ) : null}
      </div>
    </div>
  );
}
