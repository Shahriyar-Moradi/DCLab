/**
 * "Coming next" cards (D2b, founder decision 9): ONLY for pieces that are still not built. Each card says what the piece
 * will do and shows an example picture marked "Example, not your data": the picture is not read from any API, is never
 * interactive and is never offered as a real number. Pure data, so the words are checked under `npm run test:components`.
 * When a piece ships, delete its entry here and its card (the test keeps the list short and honest).
 */

export const EXAMPLE_MARK = "Example, not your data";

export type ComingNextEntry = {
  id: string;
  title: string;
  /** What the piece will do, in plain words; says that it is not built yet. */
  text: string;
  /** A status word shown beside "Coming next" (e.g. "Not configured"), when the plan names one. */
  status?: string;
  /** What today offers instead (a plain sentence, no link). */
  today: string;
  /** One sentence describing the picture for screen readers (the picture itself is hidden from them). */
  pictureLabel: string;
  /** The example picture: a few made-up rows, shown as a picture only. */
  rows: ReadonlyArray<readonly [string, string]>;
};

export const COMING_NEXT: Readonly<Record<"backups", ComingNextEntry>> = {
  backups: {
    id: "backups",
    title: "Backups",
    status: "Not configured",
    text: "Not built yet. Backups stay not configured here until DCLab runs in the cloud; this tile will then show when the platform database was last backed up, whether it worked, and when the next backup runs.",
    today: "No backup read exists in the console today, so nothing about backups is shown as a fact here.",
    pictureLabel: "An example tile with a last backup time, its result and the next backup time. The values are made up.",
    rows: [["Last backup", "Sunday 03:00, worked"], ["Next backup", "Sunday 03:00"], ["Kept for", "30 days"]],
  },
};

/** Own keys only: an id such as `__proto__` has no card. */
export function comingNext(id: string): ComingNextEntry | null {
  return Object.hasOwn(COMING_NEXT, id) ? COMING_NEXT[id as keyof typeof COMING_NEXT] : null;
}
