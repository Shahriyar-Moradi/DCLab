"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHead } from "@/components/studio/PageHead";
import { ModelInspector } from "@/app/components/studio-app/Inspectors";

export default function ModelInspectorPage() {
  const { id, modelId } = useParams<{ id: string; modelId: string }>();
  return (
    <>
      <PageHead title="Model version" eyebrow={`${modelId.slice(0, 8)}`} subtitle="What this version is, what it was built from and its champion state." actions={<Link className="btn" href={`/projects/${id}/graph`}>Back to the graph</Link>} />
      <ModelInspector projectId={id} modelVersionId={modelId} />
    </>
  );
}
