import { notFound } from "next/navigation";
import type { ReactNode } from "react";
import { ProjectHeader } from "@/app/components/studio-app/StudioParts";
import { isUuid } from "@/lib/application/command-search";

export default async function ProjectLayout({ children, params }: { children: ReactNode; params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  return <ProjectHeader projectId={id}>{children}</ProjectHeader>;
}
