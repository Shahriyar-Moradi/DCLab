"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { PageHead } from "@/components/studio/PageHead";
import { DatasetInspector } from "@/app/components/studio-app/Inspectors";

export default function DatasetInspectorPage() {
  const { id, datasetId } = useParams<{ id: string; datasetId: string }>();
  return (
    <>
      <PageHead title="Data version" subtitle="Shape, profile summary and the assistant's review, beside the rules' answer." actions={<Link className="btn" href={`/projects/${id}/graph`}>Back to lineage</Link>} />
      <DatasetInspector projectId={id} datasetId={datasetId} />
    </>
  );
}
