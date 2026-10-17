import assert from "node:assert/strict";
import test from "node:test";

import { GUIDE_STORAGE_KEY, guideHiddenSnapshot, readGuideHidden, resetGuideMemory, setGuideHidden, subscribeGuide, type GuideStorage } from "./guide-preference.ts";

function memoryStorage(): GuideStorage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
}
const blocked: GuideStorage = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };

test("the guide is shown unless the stored choice says hidden", () => {
  resetGuideMemory();
  const storage = memoryStorage();
  assert.equal(readGuideHidden(storage), false);
  assert.equal(readGuideHidden(null), false);
  storage.data.set(GUIDE_STORAGE_KEY, "hidden");
  assert.equal(readGuideHidden(storage), true);
  storage.data.set(GUIDE_STORAGE_KEY, "something else");
  assert.equal(readGuideHidden(storage), false);
});

test("hiding is remembered, tells subscribers, and showing again stores it", () => {
  resetGuideMemory();
  const storage = memoryStorage();
  let calls = 0;
  const stop = subscribeGuide(() => { calls += 1; });
  setGuideHidden(true, storage);
  assert.equal(storage.data.get(GUIDE_STORAGE_KEY), "hidden");
  assert.equal(guideHiddenSnapshot(storage), true);
  setGuideHidden(false, storage);
  assert.equal(storage.data.get(GUIDE_STORAGE_KEY), "shown");
  assert.equal(guideHiddenSnapshot(storage), false);
  stop();
  setGuideHidden(true, storage);
  assert.equal(calls, 2);
  resetGuideMemory();
});

test("blocked storage never throws: the guide shows, and a choice holds for this page load", () => {
  resetGuideMemory();
  assert.equal(readGuideHidden(blocked), false);
  assert.doesNotThrow(() => setGuideHidden(true, blocked));
  assert.equal(guideHiddenSnapshot(blocked), true);
  resetGuideMemory();
  assert.equal(guideHiddenSnapshot(blocked), false);
});
