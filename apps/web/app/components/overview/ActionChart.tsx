"use client";

import { actionTone, TONE_FILL } from "@/lib/domain";
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type ChartRow = { action: string; count: number; fill: string };

export function ActionChart({ counts }: { counts: Record<string, number> }) {
  const chart: ChartRow[] = Object.entries(counts).map(([action, count]) => ({
    action: action.replaceAll("_", " "),
    count,
    fill: TONE_FILL[actionTone(action)],
  }));

  if (!chart.length) {
    return (
      <p className="font-body text-body text-ink-muted">No decisions yet — generate one from an opportunity.</p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <XAxis dataKey="action" tick={{ fill: "var(--color-ink-muted)", fontSize: 12 }} axisLine={{ stroke: "var(--color-hairline)" }} />
        <YAxis allowDecimals={false} tick={{ fill: "var(--color-ink-muted)", fontSize: 12 }} axisLine={{ stroke: "var(--color-hairline)" }} />
        <Tooltip
          cursor={{ fill: "var(--color-navy-soft)" }}
          contentStyle={{
            background: "var(--color-paper-raised)",
            border: "1px solid var(--color-hairline)",
            borderRadius: 6,
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
