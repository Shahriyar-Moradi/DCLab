import type { ReactNode } from "react";
import { Card } from "./Card";

export function Stat({ value, label, hint }: { value: ReactNode; label: ReactNode; hint?: ReactNode }) {
  return (
    <Card>
      <div className="stat">
        <div className="v">{value}</div>
        <div className="l">{label}</div>
        {hint ? <div className="l">{hint}</div> : null}
      </div>
    </Card>
  );
}
