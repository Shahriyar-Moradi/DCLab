/**
 * Guide strip preference (V7 UI-A): shown or hidden, remembered in this browser. Pure store (no React) for
 * `useSyncExternalStore`; storage can be missing or blocked (private mode, tests), so every access is guarded
 * and the guide is shown when nothing can be read.
 */
export const GUIDE_STORAGE_KEY = "dclab-studio-guide";

export type GuideStorage = Pick<Storage, "getItem" | "setItem">;

function defaultStorage(): GuideStorage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

export function readGuideHidden(storage: GuideStorage | null = defaultStorage()): boolean {
  try {
    return storage?.getItem(GUIDE_STORAGE_KEY) === "hidden";
  } catch {
    return false;
  }
}

const listeners = new Set<() => void>();
let memory: boolean | null = null;

/** Hidden or not: the in-memory choice of this page load first (storage may refuse writes), else the stored one. */
export function guideHiddenSnapshot(storage: GuideStorage | null = defaultStorage()): boolean {
  return memory ?? readGuideHidden(storage);
}

export function setGuideHidden(hidden: boolean, storage: GuideStorage | null = defaultStorage()): void {
  memory = hidden;
  try {
    storage?.setItem(GUIDE_STORAGE_KEY, hidden ? "hidden" : "shown");
  } catch {
    // Blocked storage: the choice still holds until the page is reloaded.
  }
  for (const listener of listeners) listener();
}

export function subscribeGuide(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Test helper: forget the in-memory choice. */
export function resetGuideMemory(): void {
  memory = null;
}
