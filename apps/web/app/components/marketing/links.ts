// Set NEXT_PUBLIC_CONTACT_EMAIL to the real DCLab inbox; until then the demo
// CTA goes to the company page instead of a guessed address.
const CONTACT_EMAIL = process.env.NEXT_PUBLIC_CONTACT_EMAIL?.trim();
export const BOOK_A_DEMO_HREF = CONTACT_EMAIL
  ? `mailto:${CONTACT_EMAIL}?subject=Book%20a%20demo`
  : "/company";

export const MARKETING_NAV = [
  { href: "/company", label: "Company" },
  { href: "/solutions", label: "Solutions" },
  { href: "/platform", label: "Platform" },
  { href: "/industries", label: "Industries" },
  { href: "/resources", label: "Resources" },
  { href: "/pricing", label: "Pricing" },
] as const;
