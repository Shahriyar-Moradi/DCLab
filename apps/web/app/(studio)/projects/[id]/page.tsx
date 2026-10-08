import { notFound, redirect } from "next/navigation";
import { projectHref } from "@/lib/application/command-search";

/** A project opens on its Experiments page (ids are UUIDs; anything else is not found). */
export default async function ProjectIndex({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const href = projectHref(id, "experiments");
  if (!href) notFound();
  redirect(href);
}
