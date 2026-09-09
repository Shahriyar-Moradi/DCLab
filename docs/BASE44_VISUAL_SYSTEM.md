# Base44 visual system (Phase 1)

Reference app: `https://convivial-decide-with-clarity.base44.app`  
App title in the HTML shell: **Base44 APP**. PWA title: **DCLab**.

This document records **visual and interaction patterns only**. It does not describe, copy, or reconstruct Base44’s generated backend (entities, `/entities/User/me`, token storage, or any data APIs).

No page redesigns were made for this audit.

## How this was inspected

A live Chromium pass (click every screen, DevTools computed styles, mobile viewport screenshots) was **not available** in this session: the IDE browser tool namespace was not connected.

What was inspected instead:

1. The HTML shell of the live URL (SPA: `#root`, module `/assets/index-BYnYyn53.js`, stylesheet `/assets/index-Be6w19NY.css`).
2. The **shipped CSS** `:root` design tokens and font import.
3. **Declared Tailwind class names** in the shipped JS for layout, navigation, page chrome, cards, tables, forms, and marketing header.

Those class names are authoring sizes (for example `text-[28px]`, `w-[240px]`), not measured-on-screen pixels after zoom, subpixel rounding, or user font settings. Where a value was not in CSS or class strings, it is marked **not determined**.

Do not treat the demo numbers, customer IDs (`cus-1` …), or prediction IDs (`pred-churn` …) inside the bundle as product data. They are UI fixtures for the reference app.

## Product IA (authenticated)

Left sidebar groups, from the reference nav config:

| Group | Items (route) |
| --- | --- |
| Intelligence | Dashboard `/dashboard`, Intelligence `/intelligence`, Recommendations `/recommendations`, Simulation `/simulation`, Decisions `/decisions`, Outcomes `/outcomes`, Case Studies `/case-studies` |
| Data & Models | Data `/data`, Models `/models`, Custom Prediction `/custom-predictions` |
| Operate | Customers `/customers`, Monitoring `/monitoring`, Settings `/settings` |

Other authenticated or auth routes in the client router:

- `/login`, `/register`, `/forgot-password`, `/reset-password`
- `/decisions/summary`
- `/guided-simulation`, `/walkthrough/churn`
- Detail patterns: `/customers/:customerId`, `/data/sources/:sourceId`, `/data/features`, `/intelligence/predictions/:predictionId`, `/models/:modelId`, `/outcomes/:outcomeId`, `/recommendations/:recommendationId`, `/case-studies/:caseId`

Public marketing is the `/` landing page with in-page anchors (`#services`, `#platform`, `#case-studies`, `#ml`, `#dashboard`, `#pricing`), not separate marketing routes. Mobile marketing nav is a header accordion, not a separate URL.

## Typography

**Family:** Inter for heading, body, and display.

```text
@import "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap"

--font-heading / --font-body / --font-display: "Inter", ui-sans-serif, system-ui, sans-serif
--font-mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace
```

**Weights in the font request:** 400, 500, 600, 700. UI usage observed: 500 (nav, labels), 600 (brand, page titles, metrics), 700 (marketing wordmark, auth title).

**Smoothing:** `antialiased` on `body`.

### Declared sizes (class names, not computed)

| Role | Declared size | Weight | Tracking / leading |
| --- | --- | --- | --- |
| Marketing wordmark | `text-[18px]` | `font-bold` (700) | `tracking-tight` |
| Marketing kicker | `text-[10px]` | `font-semibold` | `tracking-[0.18em]` |
| Marketing nav / Sign in | `text-[14px]` | medium on Sign in | color `#4B5563` → `#0B1226` on hover |
| Auth title | `text-3xl` (~30px in Tailwind default) | `font-bold` | `tracking-tight` |
| Product page H1 | `text-[28px]` | `font-semibold` | `leading-tight` + `tracking-tight` |
| Page subtitle | `text-[14px]` | regular | muted; `mt-1.5` |
| Section H2 (settings cards) | `text-[15px]` | `font-semibold` | — |
| Custom-prediction H2 | `text-[17px]` | `font-semibold` | — |
| Metric value | `text-[26px]` | `font-semibold` | `tracking-tight` |
| Metric label | `text-[12.5px]` | `font-medium` | muted |
| Metric caption / table header | `text-[11.5px]` | medium / `uppercase tracking-wide` | muted |
| Sidebar brand | `text-[15px]` | `font-semibold` | `leading-none tracking-tight` |
| Sidebar tagline | `text-[11px]` | regular | `leading-none` |
| Sidebar group label | `text-[10.5px]` | `font-medium` | `uppercase tracking-wider` |
| Sidebar item | `text-[13.5px]` | `font-medium` | — |
| Collapse control | `text-[13px]` | `font-medium` | — |
| Table body | `text-[13.5px]` | — | — |
| Topbar workspace crumb | `text-[13px]` | medium + muted | — |
| Account name / role | `text-[13px]` / `text-[11px]` | medium / muted | `leading-tight` |
| Search field | `text-[13px]` | — | — |
| Keyboard hint | `text-[10px]` | `font-medium` | — |
| Primary actions | `text-[13px]` or `text-[13.5px]` | `font-medium` | — |

**Letter-spacing utilities present in CSS:** `-0.025em`, `0.025em`, `0.05em`, `0.1em`, `0.14em`, `0.16em`, `0.18em`, `0.2em`. Product headings use `tracking-tight` (typically −0.025em in Tailwind). Group labels use `tracking-wider`. Exact computed tracking on each node was **not measured**.

**Line heights:** `leading-tight` on H1; `leading-none` on the collapsed-width brand stack; `leading-tight` on the account block. Numeric line-height on body text was **not determined** beyond `body` inheriting the font with no custom `line-height` in `:root`.

## Layout

| Token | Declared value |
| --- | --- |
| Product shell | `flex h-screen bg-background overflow-hidden` |
| Sidebar expanded | `w-[240px]` |
| Sidebar collapsed | `w-[68px]` |
| Sidebar transition | `transition-[width] duration-200 ease-out` |
| Sidebar header / product topbar | `h-16` |
| Sidebar header padding | `px-4` |
| Sidebar nav | `flex-1 overflow-y-auto py-4 px-2.5 space-y-6` |
| Nav item | `gap-2.5 px-2.5 py-2 rounded-md`; icon `17px` |
| Topbar | `px-6`; search control `w-64` (16rem) × `h-9` |
| Product page canvas | `max-w-[1280px] mx-auto px-6 lg:px-10 py-8` (Monitoring, Recommendations, Decisions, Outcomes, Customers, Models, Settings) |
| Narrow form page | Custom Prediction: `max-w-[860px] mx-auto px-6 py-10` |
| Auth | `min-h-screen` centered; card `max-w-md`; card padding `p-8` |
| Marketing header inner | `max-w-[1200px] mx-auto px-5 lg:px-8 h-[64px]` |
| Marketing hero inner | same max width; `py-16 lg:py-24` |

**Page maximum widths seen in class names:** 340–680px (narrow columns), 860px (custom prediction), 1100 / 1200 / 1280px (marketing and product). Tailwind `.container` breakpoints in the CSS file: 640 / 768 / 1024px (and the usual later container steps if present in the framework layer).

**Content padding** on product pages is the `px-6 lg:px-10 py-8` wrapper, plus `mb-8` under the page header. Inner card padding is typically `p-5` (metrics) or `p-6` (settings sections). Table cells `px-5 py-3` / `py-3.5`.

### Responsive behavior (from classes, not screenshots)

- **Marketing:** `lg:flex` desktop nav and CTAs; `lg:hidden` hamburger. Open state is an in-header stack (`border-t`, `px-5 py-4`), not a slide-over drawer. Logo kicker `hidden sm:inline`.
- **Product account labels:** `hidden sm:block` (name/role hide on very small widths; avatar remains).
- **Product sidebar:** collapse is a **click toggle** (240px ↔ 68px), not a `md:`/`lg:` media query in the layout function inspected. Whether the 240px rail is usable at 375px was **not screenshot-verified**. Collapsed items set native `title={label}` for hover text.
- **Page header:** stacks `flex-col` then `sm:flex-row sm:items-end sm:justify-between`.
- **Metric / outcome grids:** `grid-cols-1 md:grid-cols-2 lg:grid-cols-3` (outcomes); model cards `sm:grid-cols-2`.
- **Tables:** wrapped in `overflow-x-auto`.
- **Filter + search on Customers:** `flex-col sm:flex-row`.

No Radix `Sheet` / `Drawer` symbols were found in the bundle. Dedicated mobile product drawer: **not identified**.

## Surfaces

HSL tokens from `:root`, with approximate hex (rounded conversion, not eyedropper):

| Token | HSL | Approx. hex | Role |
| --- | --- | --- | --- |
| `--background` | 210 40% 99% | `#FBFCFD` | Page paper (cool near-white) |
| `--foreground` | 222 47% 11% | `#0F1729` | Primary text |
| `--card` | 0 0% 100% | `#FFFFFF` | Cards, sidebar, topbar base |
| `--primary` | 222 47% 11% | `#0F1729` | Solid actions, logo tile, avatar |
| `--primary-foreground` | 0 0% 98% | `#FAFAFA` | Text on primary |
| `--secondary` | 220 14% 96% | `#F3F4F6` | Active nav fill, hover, table header |
| `--muted` | 220 14% 96% | `#F3F4F6` | Muted fills |
| `--muted-foreground` | 220 9% 46% | `#6B7280` | Secondary text, idle nav |
| `--accent` | 221 83% 53% | `#2463EB` | Active nav **icon**, focus ring |
| `--accent-foreground` | 0 0% 100% | `#FFFFFF` | On accent |
| `--destructive` | 0 72% 51% | `#DC2828` | Errors |
| `--border` / `--input` | 220 13% 91% | `#E5E7EB` | Hairlines, inputs |
| `--ring` | 221 83% 53% | `#2463EB` | Focus |
| `--success` | 142 71% 45% | `#21C45D` | Positive delta text uses `text-emerald-600` in metrics |
| `--warning` | 38 92% 50% | `#F59F0A` | Warning token; amber badge fills also appear as `bg-emerald-50` etc. |
| `--radius` | `.625rem` | 10px | Base radius |

Sidebar-specific: white background, muted idle text, `--sidebar-accent` same as secondary gray, `--sidebar-ring` = accent blue.

**Marketing extras (literal hex in classes):** page `#F9FAFB`, ink `#0B1226`, muted `#4B5563`, border `#E5E7EB`, brand blue `#2563EB`, CTA gradient `from-[#4F83F8] to-[#3B82F6]`.

### Cards

Dominant product card: `rounded-xl border border-border bg-card` (dozens of uses). Metric cards add `p-5`. Settings sections `p-6`. Hover on linked cards uses `group` (exact hover treatment **not fully extracted**).

**Glass:** restrained. Product topbar `bg-card/80 backdrop-blur-sm`. Marketing header `bg-white/90 backdrop-blur`. Sidebar is **opaque** `bg-card`, not frosted. Cards are solid white + border, not glass.

**Borders:** `border-border` everywhere in product chrome (`border-r` sidebar, `border-b` headers, `divide-y divide-border` tables).

**Shadows:** product cards are border-led, not elevation-led. Auth card `shadow-sm`. Marketing primary CTA `shadow-sm`. Heavy drop shadows were **not** the product-card language.

**Radius:**

- `--radius: 0.625rem` (10px)
- Cards: `rounded-xl` (12px in default Tailwind)
- Auth card / icon well: `rounded-2xl`
- Controls, nav items, logo tile: `rounded-md`
- Pills / marketing CTA: `rounded-full`
- Avatar: `rounded-full` 32px

Scrollbar: 10px, thumb `#d4d7de`, pill (`border-radius: 9999px`).

## Components

### Buttons

Product primary (repeated pattern):

```text
h-9 px-3.5 rounded-md bg-primary text-primary-foreground text-[13px] font-medium hover:opacity-90
```

Also `h-10` / `text-[13.5px]` on some full-width actions. **Primary fill is near-black navy**, not accent blue.

Secondary / outline: `h-9 px-3.5 rounded-md border border-border bg-card text-[13px] font-medium`.

Ghost icon buttons (topbar): `w-9 h-9 rounded-md hover:bg-secondary`.

Auth: full-width `h-12`; Google is `variant="outline"` `h-12`. Marketing CTA: `h-9` / mobile `h-10`, `rounded-full`, blue gradient, white text.

Disabled: opacity on submit while `animate-spin` + “Logging in…” / “Verifying…”.

### Inputs

Auth fields: `h-12`, left icon `pl-10`, placeholder examples `you@example.com` / `••••••••`. Labels above (`space-y-2`).

Customer list search: `h-9 px-3 rounded-md border` (inline with filters).

Native `<select>` count in the bundle was 0. Filters are **chip buttons**, not dropdowns.

Focus ring: `--ring` blue; a shadcn-style `focus:ring-2 focus:ring-ring` snippet exists on at least one control family. Exact input focus CSS for every field was **not fully inventoried**.

### Dropdowns

No native selects found. Combobox / menu primitives were **not confirmed**. Workspace crumb (“Demo Company / Production”) is static text in the topbar, not an openable switcher in the extracted layout.

### Tabs / chips

No `role="tab"` / `TabsList`. Recommendations (and similar lists) use a wrap of `h-8 px-3 rounded-md` chip buttons as a local filter, including an “All” value.

### Badges

Status chips include `h-7 px-2.5 rounded-md` with tinted fills (example: `bg-emerald-50 text-emerald-700 border border-emerald-200`). `rounded-full` appears on marketing CTAs and some pills. Tone mapping: success green, warning amber, danger red, plus muted gray.

### Tables

```text
rounded-xl border border-border bg-card overflow-hidden
table: w-full text-[13.5px]
thead: text-[11.5px] uppercase tracking-wide text-muted-foreground bg-secondary/50
th: font-medium px-5 py-3
tbody: divide-y divide-border
tr: hover:bg-secondary/30
td: px-5 py-3.5
```

Sticky header: **not observed**. Horizontal scroll on small widths: yes (`overflow-x-auto`).

### Charts

`recharts` is in the bundle (`LineChart`, `BarChart`, `AreaChart`). Stroke/fill colors were **not extracted**. Treat charts as standard Recharts on the light card surface; do not invent series or axes.

### Modals

`role="dialog"` was **not found**. `fixed inset-0` appears twice (likely overlays). There is **no shared modal system** obvious from those counts. Do not assume a drawer/modal kit.

### Drawers

Marketing mobile menu is an expanding header, not a left drawer. Product nav is a persistent aside. DCLab’s own `AppMobileDrawer` is **not** a Base44 pattern we measured.

### Tooltips

Collapsed sidebar uses the HTML `title` attribute. A dedicated tooltip component was **not confirmed** (the string `Tooltip` in the bundle is ambiguous).

### Empty states

No dedicated “No results” / “Get started” empty-state component turned up in string search. List pages in the reference app are filled with demo rows, so empty-state design is **not determined**.

### Loading states

`animate-spin` on auth submit/verify (7 occurrences). `animate-pulse` / Skeleton: **not found**. List loading chrome is **not determined**.

## Navigation

**Active item:** pathname equals the item path or `startsWith(path + "/")`. Active row: `bg-secondary text-foreground`; idle: `text-muted-foreground hover:text-foreground hover:bg-secondary/60`. Active **icon** uses `text-accent` (blue). Inactive icons inherit muted.

**Group labels:** uppercase, 10.5px, medium, `tracking-wider`, `text-muted-foreground/70`. Hidden when the sidebar is collapsed.

**Brand:** 32×32 `rounded-md bg-primary` with a white **D**; wordmark “DCLab” + “Decision Intelligence”. Links to `/dashboard`.

**Collapse:** bottom of sidebar, full-width text button, “Collapse”, swaps chevron icon.

**Account area:** right side of topbar — 32px primary avatar with initials, name + role (`sm+`). Not in the sidebar footer.

**Topbar extras (interaction, not data):** workspace crumb “Demo Company / Production”; search `Search…` + `⌘K`; two icon buttons (notifications / overflow — icons not named in this extract).

**Alerts in shell data:** the bundle includes demo alert copy (“model drift detected”, “Customer activity data delayed”, “Churn model operating normally”). Visual treatment of that list was **not fully extracted**; do not copy the copy into DCLab.

## What to copy vs what to ignore later

Copy as **visual language**: Inter, cool near-white paper, white bordered cards, 10–12px radii, navy primary buttons, blue only as accent/focus/active icon, 240px rail, 64px headers, 1280px content cap, dense 13–14px UI, chip filters, uppercase table headers.

Do not copy: Base44 auth (Google, localStorage token, `/entities/User/me`), demo CRM IDs, fabricated KPIs, NLP “describe a prediction”, six-agent stories, or any generated backend. Mapping of those concepts to DCLab is in `docs/BASE44_DCLAB_FEATURE_MATRIX.md`.
