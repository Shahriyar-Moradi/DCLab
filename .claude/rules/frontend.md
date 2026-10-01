---
paths:
  - "apps/web/**"
---

# Frontend rules

- The browser talks only to the Next.js origin; backend calls go through the BFF
  (`/api/backend/[...path]`) with the HttpOnly session. Never store tokens in JS.
- All data comes from backend hooks in `apps/web/lib/application/`; no mock,
  placeholder or hardcoded metrics. Disabled/unavailable capability is shown explicitly.
- Reuse primitives in `apps/web/app/components/ui/` and CSS variables in
  `globals.css`; do not add a second design system or raw hex colors.
- Capability-gated UI is a convenience only; the API must still deny.
- Before finishing: `npx tsc --noEmit && npm run lint && npm run test:components`.
