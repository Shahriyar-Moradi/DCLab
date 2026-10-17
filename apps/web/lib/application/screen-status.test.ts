import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { GLOSSARY, GLOSSARY_ORDER } from "../../components/studio/glossary.ts";
import { SIDEBAR_CONFIG } from "../../components/studio/sidebar-config.ts";
import {
  REAL_PAGE_WORDS, REAL_SUMMARY, SCREEN_STATUS, STATUS_WORDS, agreesWithFeatureFlag, rowHref, screenForPath, screenStatus, tagLabel, tagTone,
} from "./screen-status.ts";
import { NEXT_STEP_ORDER } from "./studio-flow.ts";

const P = "11111111-1111-4111-8111-111111111111";
const ID = "22222222-2222-4222-8222-222222222222";
/** Words that never reach a Studio screen (v6/v7 words table) plus internal names. */
const BANNED = /\b(holdout|hold-out|champion|digest|refs?|stale|releases?|manifest|package|L[0-3]|critic|verifier|proposer|jev|nooa|governance|decision records?|agents?|lead agent|tenant|service tokens?|scopes?|kill switch|incidents?|harness|gateway|lineage|candidates?|slots?|ticks?)\b/i;

test("every sidebar route, in every group and role, has a status entry", () => {
  for (const group of SIDEBAR_CONFIG) {
    for (const item of group.items) {
      const relative = !item.href.startsWith("/");
      const path = relative ? `/projects/${P}/${item.href.replace("{target}", ID)}` : item.href;
      if (item.id === "real") {
        assert.equal(screenForPath(path), null, "the status page has no tag of its own");
        continue;
      }
      const id = screenForPath(path);
      assert.ok(id, `${group.id}/${item.id} (${path}) has no status entry`);
      assert.ok(screenStatus(id), id);
    }
  }
});

test("project sub-pages map to their step's screen; unknown paths have none", () => {
  assert.equal(screenForPath(`/projects/${P}/experiments/${ID}`), "experiments");
  assert.equal(screenForPath(`/projects/${P}/experiments/compare`), "experiments");
  assert.equal(screenForPath(`/projects/${P}/pipeline/${ID}`), "experiments");
  assert.equal(screenForPath(`/projects/${P}/models/${ID}?tab=score`), "model");
  assert.equal(screenForPath(`/projects/${P}/graph`), "history");
  assert.equal(screenForPath(`/projects/${P}/goal`), "goal");
  assert.equal(screenForPath(`/projects/${P}/splits/${ID}`), "goal");
  assert.equal(screenForPath("/projects/new"), "projects");
  assert.equal(screenForPath("/settings/"), "settings");
  for (const path of ["/", "/real", "/projects/not-a-uuid/data", `/projects/${P}/nope`, `/projects/${P}/__proto__`, `/projects/${P}/constructor`, "/__proto__", "/toString"]) {
    assert.equal(screenForPath(path), null, path);
  }
});

test("lookups use own keys only", () => {
  for (const key of ["__proto__", "constructor", "toString", "hasOwnProperty"]) assert.equal(screenStatus(key), null, key);
  assert.equal(screenStatus("home")?.name, "Home");
});

test("statuses agree with the feature flags that gate the sidebar (no screen claims more than is built)", () => {
  for (const entry of SCREEN_STATUS) assert.ok(agreesWithFeatureFlag(entry), `${entry.id} disagrees with ${entry.feature}`);
  assert.equal(agreesWithFeatureFlag({ ...screenStatus("aiSettings")!, tags: [{ kind: "off" }] }), false);
  assert.equal(agreesWithFeatureFlag({ ...screenStatus("monitoring")!, tags: [{ kind: "planned", phase: 7 }] }), false);
});

test("the registry reflects the real state today, not the demo's older tags", () => {
  const main = (id: string) => screenStatus(id)!.tags[0].kind;
  for (const id of ["improve", "monitoring", "business", "usage", "model", "predictions", "history", "connect"]) assert.equal(main(id), "working", id);
  for (const id of ["notifications", "webhooks", "settings", "console", "assistant", "aiSettings"]) assert.equal(main(id), "partly", id);
  assert.match(screenStatus("improve")!.missing, /Guided/);
  assert.deepEqual(screenStatus("inbox")!.tags.map(tagLabel), ["Working today", "Partly working · Beta: items from the assistant"]);
  // D6b: both are a labelled beta, never "Working today".
  assert.deepEqual(screenStatus("aiSettings")!.tags.map(tagLabel), ["Partly working · Beta"]);
  assert.deepEqual(screenStatus("assistant")!.tags.map(tagLabel), ["Partly working · Beta"]);
});

test("ids are unique, every entry has a one-line plain sentence, and partly / planned tags say what or when", () => {
  assert.equal(new Set(SCREEN_STATUS.map((e) => e.id)).size, SCREEN_STATUS.length);
  for (const entry of SCREEN_STATUS) {
    assert.ok(entry.missing.length > 3 && !entry.missing.includes("\n"), entry.id);
    for (const tag of entry.tags) {
      if (tag.kind === "partly") assert.ok(tag.short, entry.id);
      if (tag.kind === "planned") assert.ok(Number.isInteger(tag.phase), entry.id);
    }
  }
});

test("tag words and tones", () => {
  assert.equal(tagLabel({ kind: "working" }), "Working today");
  assert.equal(tagLabel({ kind: "partly", short: "no screen yet" }), "Partly working · no screen yet");
  assert.equal(tagLabel({ kind: "planned", phase: 8 }), "Planned"); // no roadmap ids on screen
  assert.equal(tagLabel({ kind: "off" }), "Built, switched off");
  assert.deepEqual(["working", "partly", "off", "planned"].map((kind) => tagTone({ kind } as never)), ["ok", "warn", "ai", "gray"]);
});

test("the Business view row links only for people who may open it; project rows never link", () => {
  const business = screenStatus("business")!;
  assert.equal(rowHref(business, true), "/outcomes");
  assert.equal(rowHref(business, false), null);
  assert.equal(rowHref(screenStatus("data")!, true), null);
  assert.equal(rowHref(screenStatus("console")!, true), null);
});

/** Visible text and labels in the new components (JSX text, aria-labels and string props). */
function componentStrings(file: string): string[] {
  const source = readFileSync(new URL(file, import.meta.url), "utf8");
  const text = [...source.matchAll(/>([^<>{}]*[A-Za-z][^<>{}]*)</g)].map((m) => m[1].trim());
  const labels = [...source.matchAll(/aria-label=\{?[`"]([^`"]+)[`"]/g)].map((m) => m[1]);
  return [...text, ...labels].filter(Boolean);
}

test("no banned word in any new sentence, tag, label or glossary entry", () => {
  const sentences = [
    REAL_SUMMARY, ...Object.values(REAL_PAGE_WORDS),
    ...Object.values(STATUS_WORDS).flatMap((w) => [w.label, w.meaning]),
    ...SCREEN_STATUS.flatMap((e) => [e.name, e.missing, ...e.tags.map(tagLabel)]),
    ...NEXT_STEP_ORDER.flatMap((s) => [s.title, s.detail]),
    ...GLOSSARY_ORDER.flatMap((key) => [GLOSSARY[key].label, GLOSSARY[key].definition]),
    ...componentStrings("../../components/studio/ScreenStatusTag.tsx"),
    ...componentStrings("../../components/studio/GlossaryDialog.tsx"),
    ...componentStrings("../../components/studio/PageGuide.tsx"),
    ...componentStrings("../../app/(studio)/real/page.tsx"),
    ...componentStrings("../../app/components/studio-app/StudioParts.tsx").filter((s) => /Next step/.test(s)),
  ];
  assert.ok(sentences.length > 80);
  for (const sentence of sentences) assert.ok(!BANNED.test(sentence), `banned word in: ${sentence}`);
  assert.ok(!/holdout/i.test(sentences.join(" ")));
});
