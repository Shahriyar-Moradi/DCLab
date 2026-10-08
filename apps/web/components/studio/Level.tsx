export type TrustLevel = 0 | 1 | 2 | 3;

const LABELS: Record<TrustLevel, string> = {
  0: "Shadow: the AI's answer is only recorded",
  1: "Ask first",
  2: "Automatic, you can undo",
  3: "Automatic",
};

/** How much the assistant may do on its own, in words (internally trust level 0 to 3). */
export function Level({ level }: { level: TrustLevel }) {
  return (
    <span className={`lvl ${level === 0 ? "" : `l${level}`}`} title={LABELS[level]} aria-label={LABELS[level]}>
      {LABELS[level]}
    </span>
  );
}
