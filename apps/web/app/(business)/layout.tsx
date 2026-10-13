import type { ReactNode } from "react";
import { BusinessFrame } from "@/app/components/business-view/BusinessFrame";

export const metadata = { title: "DCLab · Business view" };

/** Business view routes (/outcomes, P7.6-A). The middleware allows any workspace reader; the API authorizes. */
export default function BusinessLayout({ children }: { children: ReactNode }) {
  return <BusinessFrame>{children}</BusinessFrame>;
}
