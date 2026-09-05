"use client";

import { Button } from "@/app/components/ui/Button";
import { Input } from "@/app/components/ui/Input";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { useState } from "react";
import { useCreateLabTaskFromConfig, useSession } from "@/lib/application";

export default function CreateTaskPage() {
  const [path, setPath] = useState("configs/tasks/purchase.yaml");
  const [message, setMessage] = useState("");
  const { user } = useSession();
  const createTask = useCreateLabTaskFromConfig();
  const canWrite = user?.role === "dclab_admin";
  return (
    <div className="max-w-xl">
      <PageHeader
        breadcrumbs={[
          { label: "Labs", href: "/admin/lab" },
          { label: "Tasks", href: "/admin/lab/tasks" },
          { label: "Create task" },
        ]}
        title="Create task"
        description="Load a versioned YAML task spec from the repo."
      />
      <div className="mt-6">
        <Input
          id="task-config-path"
          className="font-mono"
          label="Config path"
          value={path}
          disabled={!canWrite}
          onChange={(event) => setPath(event.target.value)}
        />
      </div>
      <Button
        className="mt-4"
        disabled={!canWrite || createTask.isPending}
        onClick={() => {
          createTask.mutate(path, {
            onSuccess: (row) => setMessage(`Created ${row.slug}`),
            onError: (err) => setMessage(err.message),
          });
        }}
      >
        {createTask.isPending ? "Saving…" : "Save task"}
      </Button>
      {!canWrite ? (
        <p className="mt-4 text-body text-ink-muted">Read-only platform access. Creating tasks requires DCLab Admin.</p>
      ) : null}
      {message ? <p className="mt-4 text-body text-ink">{message}</p> : null}
    </div>
  );
}
