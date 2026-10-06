export type TrustLevel = 0 | 1 | 2 | 3;

const LABELS: Record<TrustLevel, string> = {
  0: "L0, advise only",
  1: "L1, proposes",
  2: "L2, acts with review",
  3: "L3, acts autonomously",
};

/** Trust-level badge (L0 to L3). */
export function Level({ level }: { level: TrustLevel }) {
  return (
    <span className={`lvl ${level === 0 ? "" : `l${level}`}`} title={LABELS[level]} aria-label={LABELS[level]}>
      L{level}
    </span>
  );
}
