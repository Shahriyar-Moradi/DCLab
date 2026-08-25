"use client";

import { actionTone, TONE_FILL } from "@/lib/domain";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type ChartRow = { action: string; count: number; fill: string };

export function ActionChart({ counts, inverted = false }: { counts: Record<string, number>; inverted?: boolean }) {
  const chart: ChartRow[] = Object.entries(counts).map(([action, count]) => ({
    action: action.replaceAll("_", " "),
    count,
    fill: TONE_FILL[actionTone(action)],
  }));
  const tick = inverted ? "rgba(255,255,255,0.55)" : "var(--color-ink-muted)";
  const axis = inverted ? "rgba(255,255,255,0.12)" : "var(--color-hairline)";

  if (!chart.length) {
    return (
      <p className={inverted ? "text-sm text-white/60" : "font-body text-body text-ink-muted"}>
        No decisions yet — generate one from an opportunity.
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <XAxis dataKey="action" tick={{ fill: tick, fontSize: 12 }} axisLine={{ stroke: axis }} />
        <YAxis allowDecimals={false} tick={{ fill: tick, fontSize: 12 }} axisLine={{ stroke: axis }} />
        <Tooltip
          cursor={{ fill: inverted ? "rgba(255,255,255,0.06)" : "var(--color-navy-soft)" }}
          contentStyle={{
            background: inverted ? "#0b1930" : "var(--color-paper-raised)",
            border: "1px solid var(--color-hairline)",
            borderRadius: 12,
            color: inverted ? "#fff" : "var(--color-ink)",
          }}
        />
        <Bar dataKey="count" radius={[6, 6, 0, 0]}>
          {chart.map((entry) => (
            <Cell key={entry.action} fill={entry.fill} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
