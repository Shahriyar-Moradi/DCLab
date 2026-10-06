"use client";

import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

export type ChartTone = "accent" | "ai" | "muted";
export type ChartSeries = { key: string; label: string; tone?: ChartTone };

const COLOR: Record<ChartTone, string> = { accent: "var(--accent)", ai: "var(--ai)", muted: "var(--muted)" };
const AXIS = { fill: "var(--muted)", fontFamily: "var(--font-mono)", fontSize: 11 };

export type ChartProps = {
  kind: "line" | "bar";
  data: Array<Record<string, string | number>>;
  xKey: string;
  series: ChartSeries[];
  /** Plain-language description for assistive tech (required: charts are not self-describing). */
  summary: string;
  height?: number;
  /** Horizontal limit line (for example a drift threshold). */
  limit?: { value: number; label: string; tone?: "warn" | "crit" };
};

/** recharts styled with Studio tokens (grid = --line, axis = --muted, series = --accent/--ai). */
export function Chart({ kind, data, xKey, series, summary, height = 260, limit }: ChartProps) {
  const common = { data, margin: { top: 8, right: 12, bottom: 0, left: 0 } };
  const axes = (
    <>
      <CartesianGrid stroke="var(--line)" vertical={false} />
      <XAxis dataKey={xKey} tick={AXIS} stroke="var(--line-strong)" tickLine={false} />
      <YAxis tick={AXIS} stroke="var(--line-strong)" tickLine={false} width={44} />
      <Tooltip contentStyle={{ background: "var(--surface)", border: "1px solid var(--line)", borderRadius: 9, color: "var(--ink)" }} />
      <Legend wrapperStyle={{ color: "var(--ink)", fontSize: 13 }} />
      {limit ? (
        <ReferenceLine y={limit.value} stroke={limit.tone === "crit" ? "var(--crit)" : "var(--warn)"} strokeDasharray="5 4" label={{ value: limit.label, fill: "var(--muted)", fontSize: 11, position: "insideTopRight" }} />
      ) : null}
    </>
  );
  return (
    <div role="img" aria-label={summary} style={{ width: "100%", height }}>
      <ResponsiveContainer width="100%" height="100%" minWidth={0}>
        {kind === "line" ? (
          <LineChart {...common}>
            {axes}
            {series.map((s) => (
              <Line key={s.key} dataKey={s.key} name={s.label} stroke={COLOR[s.tone ?? "accent"]} strokeWidth={2.2} strokeDasharray={s.tone === "muted" ? "4 3" : undefined} dot={{ r: 3, fill: COLOR[s.tone ?? "accent"] }} isAnimationActive={false} />
            ))}
          </LineChart>
        ) : (
          <BarChart {...common}>
            {axes}
            {series.map((s) => (
              <Bar key={s.key} dataKey={s.key} name={s.label} fill={COLOR[s.tone ?? "accent"]} radius={[4, 4, 0, 0]} isAnimationActive={false} />
            ))}
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
