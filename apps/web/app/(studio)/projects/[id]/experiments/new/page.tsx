"use client";

import { useParams } from "next/navigation";
import { NewRunWizard } from "@/app/components/studio-app/NewRunWizard";

export default function NewRunPage() {
  const { id } = useParams<{ id: string }>();
  return <NewRunWizard projectId={id} />;
}
