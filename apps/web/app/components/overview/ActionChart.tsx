"use client";

import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const SERIES = ["var(--color-navy)", "var(--color-cyan)", "#1d4ed8", "#38bdf8", "#2563eb", "#0e7490"];

type ChartRow = { action: string; count: number; fill: string };

export function ActionChart({ counts }: { counts: Record<string, number> }) {
  const chart: ChartRow[] = Object.entries(counts)
    .sort((left, right) => right[1] - left[1])
    .map(([action, count], index) => ({
      action: action.replaceAll("_", " "),
      count,
      fill: SERIES[index % SERIES.length],
    }));

  if (!chart.length) {
    return (
      <p className="flex h-full items-center font-sans text-body text-ink-muted">
        No decisions yet — generate one from an opportunity.
      </p>
    );
  }

  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={chart} margin={{ top: 8, right: 8, left: 0, bottom: 8 }}>
        <XAxis
          dataKey="action"
          tick={{ fill: "var(--color-ink-muted)", fontSize: 12 }}
          axisLine={{ stroke: "var(--color-hairline)" }}
          tickLine={false}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fill: "var(--color-ink-muted)", fontSize: 12 }}
          axisLine={{ stroke: "var(--color-hairline)" }}
          tickLine={false}
          width={36}
        />
        <Tooltip
          cursor={{ fill: "var(--color-navy-soft)" }}
          contentStyle={{
            background: "var(--color-paper-raised)",
            border: "1px solid var(--color-hairline)",
            borderRadius: 8,
          }}
        />
        <Bar dataKey="count" name="Decisions" radius={[6, 6, 0, 0]}>
          {chart.map((entry) => (
            <Cell key={entry.action} fill={entry.fill} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
