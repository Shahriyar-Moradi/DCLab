import type { ReactNode } from "react";
import { StudioFrame } from "@/app/components/studio-app/StudioFrame";

export const metadata = { title: "DCLab Studio" };

/** Developer Studio routes (/home, /inbox, /projects). The middleware limits them to the development role. */
export default function StudioLayout({ children }: { children: ReactNode }) {
  return <StudioFrame>{children}</StudioFrame>;
}
