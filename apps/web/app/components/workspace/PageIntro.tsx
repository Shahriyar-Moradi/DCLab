import type { ReactNode } from "react";
import { PageHeader } from "@/app/components/ui/PageHeader";
import { controlClass, controlHeightClass } from "@/app/components/ui/control";

export function PageIntro({
  eyebrow,
  title,
  subtitle,
  actions,
}: {
  eyebrow?: string;
  title: string;
  subtitle?: string;
  actions?: ReactNode;
}) {
  return <PageHeader eyebrow={eyebrow} title={title} description={subtitle} actions={actions} />;
}

export const fieldControlClass = `${controlClass} ${controlHeightClass}`;
