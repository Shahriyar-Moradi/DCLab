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
        ring: "var(--color-ring)",
        success: "var(--color-green)",
        warning: "var(--color-amber)",
        danger: "var(--color-oxblood)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        body: ["var(--font-body)", "var(--font-sans)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        display: [
          "var(--text-display-size)",
          {
            lineHeight: "var(--text-display-leading)",
            fontWeight: "var(--text-display-weight)",
            letterSpacing: "var(--text-display-tracking)",
          },
        ],
        title: [
          "var(--text-title-size)",
          {
            lineHeight: "var(--text-title-leading)",
            fontWeight: "var(--text-title-weight)",
            letterSpacing: "var(--text-title-tracking)",
          },
        ],
        section: [
          "var(--text-section-size)",
          {
            lineHeight: "var(--text-section-leading)",
            fontWeight: "var(--text-section-weight)",
            letterSpacing: "var(--text-section-tracking)",
          },
        ],
        card: [
          "var(--text-card-size)",
          {
            lineHeight: "var(--text-card-leading)",
            fontWeight: "var(--text-card-weight)",
            letterSpacing: "var(--text-card-tracking)",
          },
        ],
        body: [
          "var(--text-body-size)",
          {
            lineHeight: "var(--text-body-leading)",
            fontWeight: "var(--text-body-weight)",
            letterSpacing: "var(--text-body-tracking)",
          },
        ],
        helper: [
          "var(--text-helper-size)",
          {
            lineHeight: "var(--text-helper-leading)",
            fontWeight: "var(--text-helper-weight)",
            letterSpacing: "var(--text-helper-tracking)",
          },
        ],
        label: [
          "var(--text-label-size)",
          {
            lineHeight: "var(--text-label-leading)",
            fontWeight: "var(--text-label-weight)",
            letterSpacing: "var(--text-label-tracking)",
          },
        ],
        eyebrow: [
          "var(--text-label-size)",
          {
            lineHeight: "var(--text-label-leading)",
            fontWeight: "var(--text-label-weight)",
            letterSpacing: "var(--text-label-tracking)",
          },
        ],
        nav: [
          "var(--text-nav-size)",
          {
            lineHeight: "var(--text-nav-leading)",
            fontWeight: "var(--text-nav-weight)",
            letterSpacing: "var(--text-nav-tracking)",
          },
        ],
        button: [
          "var(--text-button-size)",
          {
            lineHeight: "var(--text-button-leading)",
            fontWeight: "var(--text-button-weight)",
            letterSpacing: "var(--text-button-tracking)",
          },
        ],
        kpi: [
          "var(--text-kpi-size)",
          {
            lineHeight: "var(--text-kpi-leading)",
            fontWeight: "var(--text-kpi-weight)",
            letterSpacing: "var(--text-kpi-tracking)",
          },
        ],
        data: [
          "var(--text-meta-size)",
          {
            lineHeight: "var(--text-meta-leading)",
            fontWeight: "var(--text-meta-weight)",
            letterSpacing: "var(--text-meta-tracking)",
          },
        ],
        "data-emphasis": [
          "var(--text-meta-size)",
          {
            lineHeight: "var(--text-meta-leading)",
            fontWeight: "500",
            letterSpacing: "var(--text-meta-tracking)",
          },
        ],
      },
      borderRadius: {
        none: "0",
        sm: "var(--radius-control)",
        DEFAULT: "var(--radius-button)",
        md: "var(--radius-button)",
        lg: "var(--radius-card)",
        xl: "var(--radius-card)",
        "2xl": "var(--radius-panel)",
        "3xl": "var(--radius-panel)",
        full: "var(--radius-pill)",
      },
      boxShadow: {
        none: "none",
        xs: "var(--shadow-xs)",
        sm: "var(--shadow-sm)",
        DEFAULT: "var(--shadow-sm)",
        md: "var(--shadow-md)",
        lg: "var(--shadow-lg)",
        glass: "var(--shadow-glass)",
        brand: "var(--shadow-brand)",
      },
      maxWidth: {
        page: "var(--page-max-width)",
      },
      width: {
        sidebar: "var(--sidebar-width)",
      },
      spacing: {
        "page-x": "var(--page-pad-x)",
        "page-x-lg": "var(--page-pad-x-lg)",
        "page-y": "var(--page-pad-y)",
        sidebar: "var(--sidebar-width)",
        topbar: "var(--topbar-height)",
      },
      transitionDuration: {
        fast: "var(--motion-fast)",
        base: "var(--motion-base)",
        drawer: "var(--motion-drawer)",
      },
      transitionTimingFunction: {
        ui: "var(--ease-out)",
      },
      ringColor: {
        DEFAULT: "var(--color-ring)",
      },
      outlineColor: {
        DEFAULT: "var(--color-ring)",
      },
    },
  },
  plugins: [],
};
export default config;
