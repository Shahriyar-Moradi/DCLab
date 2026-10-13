/** Shared time formatting for Studio sentences (pure, so it runs under `npm run test:components`). */

/** A date in UTC, always with "UTC" on it: "Mon, 5 Oct 2026, 09:00 UTC"; "—" when it cannot be read. */
export function utcWhen(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.toLocaleString("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })} UTC`;
}
