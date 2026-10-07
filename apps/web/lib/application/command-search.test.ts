import assert from "node:assert/strict";
import test from "node:test";

import { createCommandSearch, plainText, projectHref, searchStudio, type SearchHit, type SearchSources } from "./command-search.ts";
import { STUDIO_BACKEND_FEATURES, studioNavigationForUser, studioRoleForUser } from "./studio-navigation.ts";

const P1 = "11111111-1111-4111-8111-111111111111";
const E1 = "22222222-2222-4222-8222-222222222222";
const D1 = "33333333-3333-4333-8333-333333333333";
const M1 = "44444444-4444-4444-8444-444444444444";

function sources(overrides: Partial<SearchSources> = {}): SearchSources & { calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    projects: async () => (calls.push("projects"), [
      { id: P1, name: "Churn <b>model</b>", slug: "churn", status: "active" },
      { id: "not-a-uuid", name: "Churn evil", slug: "evil", status: "active" },
    ]),
    experiments: async (projectId) => (calls.push(`experiments:${projectId ?? "all"}`), [
      { id: E1, status: "running", intent: "raise churn recall", project_id: P1 },
      { id: "55555555-5555-4555-8555-555555555555", status: "completed", intent: "churn without project", project_id: null },
    ]),
    decisions: async () => (calls.push("decisions"), [{ id: D1, project_id: P1, decision_type: "champion_churn", state: "accepted" }]),
    refs: async () => (calls.push("refs"), [{ ref_kind: "champion_model", target: { kind: "model_version", id: M1 } }]),
    modelVersion: async () => null,
    ...overrides,
  };
}

test("search covers projects, experiments, models and decisions with safe project hrefs", async () => {
  const hits = await searchStudio("churn", { projectId: P1 }, sources(), new AbortController().signal);
  const byKind = (kind: string) => hits.filter((h) => h.kind === kind);
  assert.deepEqual(byKind("Project").map((h) => h.href), [`/projects/${P1}/experiments`]);
  assert.deepEqual(byKind("Experiment").map((h) => h.href), [`/projects/${P1}/experiments/${E1}`]);
  assert.deepEqual(byKind("Decision").map((h) => h.href), [`/projects/${P1}/decisions`]);
  assert.equal(byKind("Model").length, 0);
  const model = await searchStudio("champion", { projectId: P1 }, sources(), new AbortController().signal);
  assert.deepEqual(model.filter((h) => h.kind === "Model").map((h) => h.href), [`/projects/${P1}/models`]);
  for (const h of hits) assert.match(h.href, /^\/projects\/[0-9a-f-]{36}\//);
});

test("names stay plain text and hostile ids never become hrefs", () => {
  assert.equal(plainText("a\u0000b\nc\u2028d"), "a b c d");
  assert.equal(plainText("x".repeat(200)).length, 120);
  for (const bad of ["javascript:alert(1)", "//evil.example", "../admin", `${P1}/../x`, ""]) {
    assert.equal(projectHref(bad, "experiments"), null, bad);
  }
  assert.equal(projectHref(P1, "experiments", "javascript:x"), null);
  assert.equal(projectHref(P1, "../admin"), null);
});

test("without a project only workspace-wide sources are read and short queries call nothing", async () => {
  const src = sources();
  await searchStudio("churn", {}, src, new AbortController().signal);
  assert.deepEqual(src.calls.sort(), ["experiments:all", "projects"]);
  const quiet = sources();
  assert.deepEqual(await searchStudio(" c ", { projectId: P1 }, quiet, new AbortController().signal), []);
  assert.deepEqual(quiet.calls, []);
});

test("a failing source does not hide the others; an exact model id is looked up", async () => {
  const hits = await searchStudio(M1, {}, sources({
    projects: async () => { throw new Error("403"); },
    modelVersion: async (id) => ({ id, version: "v2", project_id: P1 }),
  }), new AbortController().signal);
  assert.deepEqual(hits.map((h) => [h.kind, h.href]), [["Model", `/projects/${P1}/models`]]);
});

test("debounce: only the last query runs; a newer query aborts the stale request", async () => {
  const timers: Array<() => void> = [];
  const signals: AbortSignal[] = [];
  const results: Array<[SearchHit[], string]> = [];
  const search = createCommandSearch({
    sources: sources({
      projects: (signal) => {
        signals.push(signal);
        return new Promise((resolve, reject) => {
          signal.addEventListener("abort", () => reject(Object.assign(new Error("aborted"), { name: "AbortError" })));
          setTimeout(() => resolve([{ id: P1, name: "Churn", slug: "churn", status: "active" }]), 5);
        });
      },
    }),
    onResults: (hits, query) => results.push([hits, query]),
    setTimer: (fn) => (timers.push(fn), timers.length),
    clearTimer: (handle) => { timers[(handle as number) - 1] = () => assert.fail("cancelled timer fired"); },
  });
  search.search("ch");
  search.search("chu");
  assert.equal(timers.length, 2);
  timers[1]!();
  search.search("churn");
  assert.equal(signals[0]!.aborted, true, "in-flight request aborted by the newer query");
  timers[2]!();
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.deepEqual(results.map(([, q]) => q), ["churn"]);
  assert.equal(results[0]![0][0]!.href, `/projects/${P1}/experiments`);
  search.search("x");
  assert.deepEqual(results.at(-1), [[], "x"]);
});

test("Studio sidebar: project pages only inside a project and only with a backend", () => {
  const developer = { capabilities: { development_access: true } };
  assert.equal(studioRoleForUser({ capabilities: { application_access: true } }), null);
  assert.deepEqual(studioNavigationForUser({ capabilities: { application_access: true } }), []);
  assert.equal(studioRoleForUser({ capabilities: { platform_read: true, development_access: true } }), "admin");
  const outside = studioNavigationForUser(developer);
  assert.deepEqual(outside.map((g) => g.id), ["workspace"]);
  assert.deepEqual(outside[0]!.items.map((i) => i.href), ["/home", "/projects", "/inbox", "/agents"]);
  const inside = studioNavigationForUser(developer, P1).find((g) => g.id === "project")!;
  assert.deepEqual(inside.items.map((i) => i.id), ["pipeline", "graph", "data", "experiments", "models", "decisions"]);
  assert.ok(inside.items.every((i) => i.href.startsWith(`/projects/${P1}/`)));
  for (const hidden of ["lab", "improve", "monitoring"] as const) assert.equal(STUDIO_BACKEND_FEATURES[hidden], false);
  assert.equal(STUDIO_BACKEND_FEATURES.assistant, false);
  assert.deepEqual(studioNavigationForUser(developer, "../../evil?x=1").map((g) => g.id), ["workspace"]);
});
