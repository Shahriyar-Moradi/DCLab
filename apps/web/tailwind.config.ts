import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "./lib/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        paper: "var(--color-paper)",
        "paper-raised": "var(--color-paper-raised)",
        ink: "var(--color-ink)",
        "ink-muted": "var(--color-ink-muted)",
        navy: "var(--color-navy)",
        "navy-soft": "var(--color-navy-soft)",
        green: "var(--color-green)",
        amber: "var(--color-amber)",
        oxblood: "var(--color-oxblood)",
        hairline: "var(--color-hairline)",
        brand: "var(--color-brand)",
        cyan: "var(--color-cyan)",
        midnight: "var(--color-midnight)",
      },
      fontFamily: {
        display: ["var(--font-body)", "sans-serif"],
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      fontSize: {
        title: ["2.25rem", { lineHeight: "1.15", fontWeight: "700" }],
        section: ["1.5rem", { lineHeight: "1.25", fontWeight: "700" }],
        body: ["0.9375rem", { lineHeight: "1.6", fontWeight: "400" }],
        eyebrow: [
          "0.7rem",
          { lineHeight: "1.2", fontWeight: "700", letterSpacing: "0.08em" },
        ],
        data: ["0.875rem", { lineHeight: "1.4", fontWeight: "400" }],
        "data-emphasis": ["0.5rem", { lineHeight: "1.4", fontWeight: "500" }],
      },
    },
  },
  plugins: [],
};
export default config;
