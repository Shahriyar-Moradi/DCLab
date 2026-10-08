import assert from "node:assert/strict";
import test from "node:test";

import { NO_STUDIO_CAPABILITIES, SIDEBAR_CONFIG, resolveSidebar } from "./sidebar-config.ts";

const ids = (groups: ReturnType<typeof resolveSidebar>) => groups.flatMap((g) => g.items.map((i) => i.id));

test("with no backend capabilities every item is hidden", () => {
  for (const role of ["developer", "admin", "client", "operator"] as const) {
    assert.deepEqual(resolveSidebar(role, { capabilities: NO_STUDIO_CAPABILITIES }), []);
    assert.deepEqual(resolveSidebar(role), []);
  }
});

test("items need both the role and the backend feature", () => {
  const caps = { graph: true, client_outcomes: true, home: false };
  assert.deepEqual(ids(resolveSidebar("developer", { capabilities: caps })), ["graph"]);
  assert.deepEqual(ids(resolveSidebar("client", { capabilities: caps })), ["client-outcomes"]);
  assert.deepEqual(ids(resolveSidebar("operator", { capabilities: caps })), []);
});

test("showUnavailable keeps role scoping and names the phase that ships the item", () => {
  const groups = resolveSidebar("client", { showUnavailable: true });
  assert.deepEqual(ids(groups), ["client-outcomes", "client-predictions", "client-questions"]);
  assert.ok(groups.every((g) => g.items.every((i) => !i.available && /^P\d/.test(i.phase))));
  assert.ok(!ids(resolveSidebar("client", { showUnavailable: true })).includes("graph"));
});

test("project-relative hrefs take the project base; workspace hrefs stay absolute", () => {
  const groups = resolveSidebar("developer", { capabilities: { home: true, graph: true }, projectBase: "/projects/p1/" });
  const hrefs = Object.fromEntries(groups.flatMap((g) => g.items.map((i) => [i.id, i.href])));
  assert.deepEqual(hrefs, { home: "/home", graph: "/projects/p1/graph" });
});

test("config ids are unique and every item has a phase", () => {
  const all = SIDEBAR_CONFIG.flatMap((g) => g.items);
  assert.equal(new Set(all.map((i) => i.id)).size, all.length);
  assert.ok(all.every((i) => i.phase.length > 0 && i.roles.length > 0));
});

test("an unsafe project base drops project-scoped items instead of building a hostile href", () => {
  for (const base of ["javascript:alert(1)", "//evil.example", "/p?x=1", "/p#y"]) {
    const groups = resolveSidebar("developer", { capabilities: { home: true, graph: true }, projectBase: base });
    assert.deepEqual(groups.flatMap((g) => g.items.map((i) => i.href)), ["/home"], base);
  }
});
