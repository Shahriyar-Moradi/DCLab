"use client";

import { buttonClassName } from "@/app/components/ui/Button";
import { CollectionSearch } from "@/app/components/ui/CollectionSearch";
import { DataTable } from "@/app/components/ui/DataTable";
import { ErrorState } from "@/app/components/ui/ErrorState";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { Skeleton } from "@/app/components/ui/Skeleton";
import { filterByText } from "@/app/components/ui/localCollection";
import { useLabTasks } from "@/lib/application";
import Link from "next/link";
import { useState } from "react";

export default function LabTasksPage() {
  const query = useLabTasks();
  const [queryText, setQueryText] = useState("");
  if (query.isPending) {
    return (
      <div>
        <TasksHeader />
        <Skeleton className="h-64" />
      </div>
    );
  }
  if (query.isError) return <ErrorState body="Could not load tasks." onRetry={() => void query.refetch()} />;
  const allRows = query.data ?? [];
  const rows = filterByText(allRows, queryText, (task) => [task.slug, task.name, task.task_type, task.description]);
  return (
    <div>
      <TasksHeader />
      <div className="mt-8">
        <CollectionSearch value={queryText} onChange={setQueryText} shown={rows.length} total={allRows.length} />
        <DataTable
          columns={[
            { id: "slug", header: "Slug", mono: true, cell: (task) => task.slug },
            { id: "name", header: "Name", cell: (task) => task.name },
            { id: "type", header: "Type", mono: true, cell: (task) => task.task_type },
            { id: "description", header: "Description", cell: (task) => task.description },
          ]}
          rows={rows}
          rowKey={(task) => task.id}
          emptyTitle="No tasks"
          emptyBody={queryText.trim() ? "Nothing on this list matches that filter." : "Load a versioned YAML spec to register a lab task."}
        />
      </div>
    </div>
  );
}

function TasksHeader() {
  return (
    <PageHeader
      breadcrumbs={[{ label: "Labs", href: "/admin/lab" }, { label: "Tasks" }]}
      title="Tasks"
      description="Versioned task specs used by lab experiments."
      actions={
        <Link href="/admin/lab/tasks/create" className={buttonClassName({ variant: "secondary" })}>
          Create from YAML
        </Link>
      }
    />
  );
}
