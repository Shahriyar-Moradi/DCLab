"use client";

import { Button } from "@/app/components/ui/Button";
import { apiPost } from "@/lib/infrastructure";
import { LabTaskSchema } from "@/lib/domain";
import { useState } from "react";
import { useSession } from "@/lib/application";

export default function CreateTaskPage() {
  const [path, setPath] = useState("configs/tasks/purchase.yaml");
  const [message, setMessage] = useState("");
  const { user } = useSession();
  const canWrite = user?.role === "dclab_admin";
  return (
    <div className="max-w-xl">
      <h1 className="font-display text-title text-ink">Create task</h1>
      <p className="mt-2 font-body text-body text-ink-muted">Load a versioned YAML task spec from the repo.</p>
      <label className="mt-6 block font-body text-body text-ink">
        Config path
        <input
          className="mt-2 w-full rounded border border-hairline bg-paper-raised px-3 py-2 font-mono text-data"
          value={path}
          disabled={!canWrite}
          onChange={(event) => setPath(event.target.value)}
        />
      </label>
      <Button
        className="mt-4"
        disabled={!canWrite}
        onClick={() => {
          void apiPost(`/admin/tasks/from-config?path=${encodeURIComponent(path)}`, LabTaskSchema, {})
            .then((row) => setMessage(`Created ${row.slug}`))
            .catch((err: Error) => setMessage(err.message));
        }}
      >
        Save task
      </Button>
      {!canWrite ? <p className="mt-4 text-body text-ink-muted">Read-only platform access. Creating tasks requires DCLab Admin.</p> : null}
      {message ? <p className="mt-4 font-body text-body text-ink">{message}</p> : null}
    </div>
  );
}
