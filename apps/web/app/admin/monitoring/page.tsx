"use client";

import { Badge } from "@/app/components/ui/Badge";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { Table, Td, Th } from "@/app/components/ui/Table";
import { useAdminMonitoring } from "@/lib/application";

export default function MonitoringPage() {
  const query = useAdminMonitoring();

  if (query.isPending) return <Skeleton className="h-64" />;
  if (query.isError || !query.data) {
    return <ErrorState body="Could not load monitoring." onRetry={() => void query.refetch()} />;
  }

  const { retrain_events: retrainEvents, dataset_health: datasetHealth, drift_detection_note: driftNote } = query.data;

  return (
    <div>
      <p className="font-body text-eyebrow uppercase tracking-[0.06em] text-ink-muted">DCLab Admin</p>
      <h1 className="mt-2 font-display text-title text-ink">Monitoring</h1>
      <p className="mt-2 max-w-2xl font-body text-body text-ink-muted">
        Retrain history, metric deltas between consecutive runs of the same task or use case, and
        dataset sync health.
      </p>

      <h2 className="mt-10 font-display text-section text-ink">Retrain history &amp; metric deltas</h2>
      <div className="mt-4">
        <Table>
          <thead>
            <tr>
              <Th>Source</Th>
              <Th>Name</Th>
              <Th>Status</Th>
              <Th>Metric change</Th>
              <Th>Created</Th>
            </tr>
          </thead>
          <tbody>
            {retrainEvents.map((event) => (
              <tr key={`${event.source}:${event.id}`}>
                <Td>
                  <Badge
                    tone={event.source === "experiment" ? "green" : event.source === "simulation" ? "amber" : "oxblood"}
                  >
                    {event.source}
                  </Badge>
                </Td>
                <Td>{event.name}</Td>
                <Td>{event.status}</Td>
                <Td mono>
                  {Object.keys(event.metric_deltas).length === 0
                    ? "first run of this task/use case"
                    : Object.entries(event.metric_deltas)
                        .map(
                          ([key, delta]) =>
                            `${key}: ${delta.previous.toFixed(3)} \u2192 ${delta.current.toFixed(3)} (${
                              delta.delta >= 0 ? "+" : ""
                            }${delta.delta.toFixed(3)})`,
                        )
                        .join(" · ")}
                </Td>
                <Td mono>{new Date(event.created_at).toLocaleString()}</Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {retrainEvents.length === 0 ? (
          <p className="mt-6 font-body text-body text-ink-muted">No retrains recorded yet.</p>
        ) : null}
      </div>

      <h2 className="mt-10 font-display text-section text-ink">Dataset sync health</h2>
      <div className="mt-4">
        <Table>
          <thead>
            <tr>
              <Th>Dataset</Th>
              <Th>Rows</Th>
              <Th>Columns</Th>
              <Th>Last profiled</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {datasetHealth.map((dataset) => (
              <tr key={dataset.id}>
                <Td>{dataset.name}</Td>
                <Td mono>{dataset.row_count}</Td>
                <Td mono>{dataset.column_count}</Td>
                <Td mono>{dataset.last_profiled_at ? new Date(dataset.last_profiled_at).toLocaleString() : "—"}</Td>
                <Td>
                  <Badge tone={dataset.status === "healthy" ? "green" : dataset.status === "not_profiled" ? "amber" : "oxblood"}>
                    {dataset.status.replaceAll("_", " ")}
                  </Badge>
                </Td>
              </tr>
            ))}
          </tbody>
        </Table>
        {datasetHealth.length === 0 ? (
          <p className="mt-6 font-body text-body text-ink-muted">No datasets ingested yet.</p>
        ) : null}
      </div>

      <p className="mt-10 rounded bg-paper-raised p-4 font-body text-body text-ink-muted">{driftNote}</p>
    </div>
  );
}
