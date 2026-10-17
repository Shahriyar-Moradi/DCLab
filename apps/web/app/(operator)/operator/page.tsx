"use client";

import { Suspense } from "react";
import { OperatorConsole } from "@/app/components/operator/OperatorConsole";

export default function OperatorPage() {
  return (
    <Suspense fallback={<p role="status">Loading the operator console…</p>}>
      <OperatorConsole />
    </Suspense>
  );
}
