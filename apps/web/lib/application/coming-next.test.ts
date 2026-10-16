import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { COMING_NEXT, EXAMPLE_MARK, comingNext } from "./coming-next.ts";
import { STUDIO_BACKEND_FEATURES } from "./studio-navigation.ts";

const BANNED = /\b(holdout|champion|digest|refs?|releases?|manifest|L[0-3]|jev|nooa|governance|tenant|harness|P\d+\.\d+)\b/i;
const openapi = JSON.parse(readFileSync(new URL("../infrastructure/v1/openapi.json", import.meta.url), "utf8")) as { paths: Record<string, unknown> };

test("coming next is only for the two unbuilt pieces, each says so and marks its picture as an example", () => {
  assert.deepEqual(Object.keys(COMING_NEXT).sort(), ["backups", "earnedAutonomy"]);
  assert.equal(EXAMPLE_MARK, "Example, not your data");
  for (const entry of Object.values(COMING_NEXT)) {
    assert.match(entry.text, /^Not built yet\./);
    assert.match(entry.pictureLabel, /made up/);
    assert.ok(entry.rows.length > 0 && entry.rows.length <= 4);
    for (const text of [entry.title, entry.text, entry.today, entry.pictureLabel, ...entry.rows.flat()]) assert.doesNotMatch(text, BANNED, text);
  }
});

test("the cards stay honest: no backup read exists, and the assistant and AI settings are still switched off", () => {
  assert.deepEqual(Object.keys(openapi.paths).filter((p) => /backup/i.test(p)), []);
  // When the assistant beta lands (or the AI settings screen), delete the earned-autonomy card.
  assert.equal(STUDIO_BACKEND_FEATURES.assistant, false);
  assert.equal(STUDIO_BACKEND_FEATURES.governance, false);
});

test("lookups use own keys only", () => {
  assert.equal(comingNext("__proto__"), null);
  assert.equal(comingNext("constructor"), null);
  assert.equal(comingNext("backups")?.title, "Backups");
  assert.equal(comingNext("backups")?.status, "Not configured");
});
