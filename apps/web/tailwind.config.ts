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
        midnight: "var(--color-midnight)",
        brand: "var(--color-brand)",
        cyan: "var(--color-cyan)",
      },
      fontFamily: {
        display: ["var(--font-display)", "serif"],
        body: ["var(--font-body)", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      fontSize: {
        title: ["2rem", { lineHeight: "1.15", fontWeight: "600" }],
        section: ["1.5rem", { lineHeight: "1.2", fontWeight: "500" }],
        body: ["0.9375rem", { lineHeight: "1.5", fontWeight: "400" }],
        eyebrow: [
          "0.75rem",
          { lineHeight: "1.2", fontWeight: "600", letterSpacing: "0.06em" },
        ],
        data: ["0.875rem", { lineHeight: "1.4", fontWeight: "400" }],
        "data-emphasis": ["0.5rem", { lineHeight: "1.4", fontWeight: "500" }],
      },
      borderRadius: {
        DEFAULT: "6px",
        sm: "6px",
        md: "6px",
        lg: "6px",
        xl: "6px",
        "2xl": "6px",
        "3xl": "6px",
      },
    },
  },
  plugins: [],
};
export default config;
