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
  const caps = { data: true, client_outcomes: true, home: false };
  assert.deepEqual(ids(resolveSidebar("developer", { capabilities: caps })), ["data"]);
  assert.deepEqual(ids(resolveSidebar("client", { capabilities: caps })), ["client-outcomes"]);
  assert.deepEqual(ids(resolveSidebar("operator", { capabilities: caps })), []);
});

test("showUnavailable keeps role scoping and names the phase that ships the item", () => {
  const groups = resolveSidebar("client", { showUnavailable: true });
  assert.deepEqual(ids(groups), ["client-outcomes", "client-predictions", "client-questions"]);
  assert.ok(groups.every((g) => g.items.every((i) => !i.available && /^P\d/.test(i.phase))));
  assert.ok(!ids(resolveSidebar("client", { showUnavailable: true })).includes("data"));
});

test("project-relative hrefs take the project base; workspace hrefs stay absolute", () => {
  const groups = resolveSidebar("developer", { capabilities: { home: true, data: true }, projectBase: "/projects/p1/" });
  const hrefs = Object.fromEntries(groups.flatMap((g) => g.items.map((i) => [i.id, i.href])));
  assert.deepEqual(hrefs, { home: "/home", data: "/projects/p1/data" });
});

test("config ids are unique and every item has a phase", () => {
  const all = SIDEBAR_CONFIG.flatMap((g) => g.items);
  assert.equal(new Set(all.map((i) => i.id)).size, all.length);
  assert.ok(all.every((i) => i.phase.length > 0 && i.roles.length > 0));
});

test("an unsafe project base drops project-scoped items instead of building a hostile href", () => {
  for (const base of ["javascript:alert(1)", "//evil.example", "/p?x=1", "/p#y"]) {
    const groups = resolveSidebar("developer", { capabilities: { home: true, data: true }, projectBase: base });
    assert.deepEqual(groups.flatMap((g) => g.items.map((i) => i.href)), ["/home"], base);
  }
});

const SPLIT = "22222222-2222-4222-8222-222222222222";
const MODEL = "33333333-3333-4333-8333-333333333333";
const allOn = { home: true, inbox: true, projects: true, data: true, goal: true, experiments: true, models: true, predictions: true, decisions: true, agents: true };

test("v7 sidebar: grouped, in the v7 order and words", () => {
  const groups = resolveSidebar("developer", { capabilities: allOn, projectBase: "/projects/p1", targets: { split_plan: SPLIT, champion_model: MODEL } });
  assert.deepEqual(groups.map((g) => [g.label, g.items.map((i) => i.label)]), [
    ["Workspace", ["Home", "Inbox", "Projects"]],
    ["Project", ["Data", "Goal & test design", "Experiments", "Model", "Predictions", "History"]],
    ["Admin", ["Connect"]],
  ]);
});

test("Goal and Predictions need the id of the test design / model in use, else they are left out", () => {
  const without = resolveSidebar("developer", { capabilities: allOn, projectBase: "/projects/p1" });
  assert.ok(!ids(without).includes("goal") && !ids(without).includes("predictions"));
  const bad = resolveSidebar("developer", { capabilities: allOn, projectBase: "/projects/p1", targets: { split_plan: "../x", champion_model: null } });
  assert.ok(!ids(bad).includes("goal") && !ids(bad).includes("predictions"));
  const hrefs = Object.fromEntries(resolveSidebar("developer", { capabilities: allOn, projectBase: "/projects/p1", targets: { split_plan: SPLIT, champion_model: MODEL } }).flatMap((g) => g.items.map((i) => [i.id, i.href])));
  assert.equal(hrefs.goal, `/projects/p1/splits/${SPLIT}`);
  assert.equal(hrefs.predictions, `/projects/p1/models/${MODEL}?tab=score`);
});

test("pages that are not built (Improve, Monitoring, AI settings, Settings) stay hidden with the default capabilities", async () => {
  const { STUDIO_BACKEND_FEATURES } = await import("../../lib/application/studio-navigation.ts");
  const shown = ids(resolveSidebar("developer", { capabilities: STUDIO_BACKEND_FEATURES, projectBase: "/projects/p1", targets: { split_plan: SPLIT, champion_model: MODEL } }));
  for (const hidden of ["improve", "monitoring", "governance", "settings", "lab", "pipeline", "graph"]) assert.ok(!shown.includes(hidden), hidden);
});
