"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHead } from "@/components/studio/PageHead";
import { DatasetInspector } from "@/app/components/studio-app/Inspectors";

export default function DatasetInspectorPage() {
  const { id, datasetId } = useParams<{ id: string; datasetId: string }>();
  return (
    <>
      <PageHead title="Dataset version" eyebrow={`${datasetId.slice(0, 8)}`} subtitle="Shape, digest, profile summary and the AI investigation, beside the rule's answer." actions={<Link className="btn" href={`/projects/${id}/graph`}>Back to the graph</Link>} />
      <DatasetInspector projectId={id} datasetId={datasetId} />
    </>
  );
}
