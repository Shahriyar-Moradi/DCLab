"use client";

import { Area, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

const SERIES = [
  { month: "Feb", actual: 1.4, forecast: null },
  { month: "Mar", actual: 1.55, forecast: null },
  { month: "Apr", actual: 1.72, forecast: null },
  { month: "May", actual: 1.95, forecast: null },
  { month: "Jun", actual: 2.15, forecast: 2.15 },
  { month: "Jul", actual: null, forecast: 2.32 },
  { month: "Aug", actual: null, forecast: 2.48 },
];

export function RevenueForecastChart() {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <ComposedChart data={SERIES} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="forecastFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#38BDF8" stopOpacity={0.35} />
            <stop offset="100%" stopColor="#2563EB" stopOpacity={0} />
          </linearGradient>
        </defs>
        <XAxis dataKey="month" tick={{ fill: "rgba(255,255,255,0.55)", fontSize: 12 }} axisLine={false} tickLine={false} />
        <YAxis hide domain={[1.2, 2.7]} />
        <Tooltip
          contentStyle={{
            background: "#0b1930",
            border: "1px solid rgba(255,255,255,0.12)",
            borderRadius: 12,
            color: "#fff",
          }}
          formatter={(value) => (value == null ? "" : `$${Number(value).toFixed(2)}M`)}
        />
        <Area type="monotone" dataKey="actual" stroke="none" fill="url(#forecastFill)" connectNulls={false} />
        <Line
          type="monotone"
          dataKey="actual"
          stroke="#38BDF8"
          strokeWidth={2.5}
          dot={false}
          connectNulls={false}
        />
        <Line
          type="monotone"
          dataKey="forecast"
          stroke="#38BDF8"
          strokeWidth={2.5}
          strokeDasharray="6 6"
          dot={false}
          connectNulls
        />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
