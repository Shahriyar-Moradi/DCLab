import type { ReactNode } from "react";
import { OperatorFrame } from "@/app/components/operator/OperatorFrame";

export const metadata = { title: "DCLab · Operator console" };

/** Operator console (/operator, P8.5-UI). The middleware allows platform administrators only; the API authorizes. */
export default function OperatorLayout({ children }: { children: ReactNode }) {
  return <OperatorFrame>{children}</OperatorFrame>;
}
