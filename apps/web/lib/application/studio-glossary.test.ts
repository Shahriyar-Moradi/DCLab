import assert from "node:assert/strict";
import test from "node:test";

import { GLOSSARY, GLOSSARY_ORDER } from "../../components/studio/glossary.ts";

const REQUIRED = ["cv", "finalTest", "prauc", "recall", "precision", "threshold", "classWeights", "baseline", "leakage", "drift", "psi",
  "lightgbm", "catboost", "fold", "trustChecks", "accessToken"] as const;
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

test("the glossary panel lists the 16 v7 terms once each, in order, from the same entries as the tooltips", () => {
  assert.equal(GLOSSARY_ORDER.length, 16);
  assert.equal(new Set(GLOSSARY_ORDER).size, 16);
  assert.deepEqual([...GLOSSARY_ORDER].sort(), Object.keys(GLOSSARY).sort());
  assert.deepEqual(GLOSSARY_ORDER.map((key) => GLOSSARY[key].label), [
    "cross-validation", "PR-AUC", "recall", "precision", "threshold", "class weights", "baseline", "leakage",
    "drift", "PSI", "LightGBM", "CatBoost", "fold", "trust checks", "access token", "final test set (used once)",
  ]);
  for (const key of ["__proto__", "constructor", "toString"]) assert.equal(Object.hasOwn(GLOSSARY, key), false, key);
});
