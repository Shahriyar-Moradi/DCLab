export type SignalTone = "green" | "amber" | "oxblood";

export function actionTone(action: string): SignalTone {
  const key = action.toUpperCase();
  if (key === "CONTACT_TODAY") return "green";
  if (key === "NO_ACTION") return "oxblood";
  return "amber";
}

export function confidenceTone(confidence: number): SignalTone {
  if (confidence >= 0.75) return "green";
  if (confidence >= 0.5) return "amber";
  return "oxblood";
}

export function confidenceBand(confidence: number): "High" | "Medium" | "Low" {
  if (confidence >= 0.75) return "High";
  if (confidence >= 0.5) return "Medium";
  return "Low";
}

export function toneFromConfidenceBand(band: "High" | "Medium" | "Low"): SignalTone {
  if (band === "High") return "green";
  if (band === "Medium") return "amber";
  return "oxblood";
}

export function actionLabel(action: string): string {
  return action.replaceAll("_", " ");
}

export const TONE_FILL: Record<SignalTone, string> = {
  green: "var(--color-green)",
  amber: "var(--color-amber)",
  oxblood: "var(--color-oxblood)",
};

export const TONE_TEXT: Record<SignalTone, string> = {
  green: "text-green",
  amber: "text-amber",
  oxblood: "text-oxblood",
};

export const TONE_BG: Record<SignalTone, string> = {
  green: "bg-green",
  amber: "bg-amber",
  oxblood: "bg-oxblood",
};
