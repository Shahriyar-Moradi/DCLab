import assert from "node:assert/strict";
import test from "node:test";

import { GLOSSARY } from "../../components/studio/glossary.ts";

const REQUIRED = ["cv", "finalTest", "prauc", "recall", "precision", "threshold", "classWeights", "baseline", "leakage", "drift", "psi"] as const;
const BANNED = /\b(holdout|champion|digest|ref|refs|stale|L[0-3]|critic|verifier|proposer|jev|governance|decision record)\b/i;

test("the glossary covers every required term with a one-sentence plain definition", () => {
  for (const key of REQUIRED) {
    const entry = GLOSSARY[key];
    assert.ok(entry && entry.label.length > 1 && entry.definition.length > 20, key);
    assert.ok(!BANNED.test(entry.label) && !BANNED.test(entry.definition), `${key} uses a banned word`);
  }
});

test("the final test set is always described as used once", () => {
  assert.match(GLOSSARY.finalTest.label, /final test set \(used once\)/);
  assert.match(GLOSSARY.finalTest.definition, /once/);
});
