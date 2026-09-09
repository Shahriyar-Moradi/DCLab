import { Badge } from "./Badge";
import type { SignalTone } from "@/lib/domain";

const SUCCESS = new Set(["completed", "verified", "success", "active", "enabled", "pass", "ready"]);
const DANGER = new Set(["failed", "error", "rejected", "disabled", "fail", "cancelled"]);

export function statusTone(status: string): SignalTone {
  const normalized = status.toLowerCase();
  if (SUCCESS.has(normalized)) return "green";
  if (DANGER.has(normalized)) return "oxblood";
  return "amber";
}

export function StatusBadge({
  status,
  tone,
  emphasis = "soft",
}: {
  status: string;
  tone?: SignalTone;
  emphasis?: "solid" | "soft";
}) {
  return (
    <Badge tone={tone ?? statusTone(status)} emphasis={emphasis}>
      {status}
    </Badge>
  );
}
