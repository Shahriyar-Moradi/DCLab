import assert from "node:assert/strict";
import test from "node:test";

import {
  PREVIEW_NOTE, VIEW_AS_OPTIONS, VIEW_AS_STORAGE_KEY, canUseViewAs, previewCanOpen, previewGateText, previewNavigation, previewPrincipal, restorePreview, storePreview,
} from "./view-as.ts";

const PROJECT = "11111111-2222-4333-8444-555555555555";
const ids = (groups: ReturnType<typeof previewNavigation>) => groups.flatMap((group) => group.items.map((item) => item.id));

function memoryStorage(): Storage & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return {
    data,
    get length() { return data.size; },
    clear: () => data.clear(),
    key: (i: number) => [...data.keys()][i] ?? null,
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

test("only admins of the workspace get the control", () => {
  assert.equal(canUseViewAs({ capabilities: { workspace_manage_members: true } }), true);
  const others: Array<Record<string, boolean>> = [{}, { workspace_read: true, workspace_execute_ml: true, development_access: true }, { platform_read: true }, { workspace_manage_members: false }];
  for (const caps of others) assert.equal(canUseViewAs({ capabilities: caps }), false);
  assert.equal(canUseViewAs(null), false);
});

test("the options use the role names and say it is a preview", () => {
  assert.deepEqual(VIEW_AS_OPTIONS.map((o) => o.label), ["Admin (your own view)", "Data scientist", "Analyst (view only)", "Business user"]);
  assert.match(PREVIEW_NOTE, /you still have admin rights; nothing is changed/);
});

test("previewed sidebars: data scientists and analysts see the Studio, business users only the Business view", () => {
  for (const role of ["data_scientist", "analyst"] as const) {
    const nav = ids(previewNavigation(role, PROJECT));
    for (const item of ["home", "inbox", "projects", "data", "experiments", "models", "settings", "business-view"]) assert.ok(nav.includes(item), `${role} misses ${item}`);
    // No platform rights in a preview: the operator console link never shows.
    assert.ok(!nav.includes("op-overview"));
  }
  const business = previewNavigation("business_user", PROJECT);
  assert.deepEqual(ids(business), ["client-outcomes", "client-questions"]);
  assert.equal(business[0].items[0].href, "/outcomes");
});

test("screen availability follows the same route rules the middleware uses", () => {
  for (const path of ["/home", "/settings", `/projects/${PROJECT}/data`, "/outcomes"]) {
    assert.equal(previewCanOpen("data_scientist", path), true, path);
    assert.equal(previewCanOpen("analyst", path), true, path);
  }
  for (const path of ["/home", "/settings", `/projects/${PROJECT}/data`, "/inbox", "/operator"]) assert.equal(previewCanOpen("business_user", path), false, path);
  assert.equal(previewCanOpen("business_user", "/outcomes"), true);
  assert.equal(previewCanOpen("data_scientist", "/operator"), false);
  assert.match(previewGateText("business_user").title, /not open to the Business user role/);
});

test("the stand-in principal is a fresh object every time", () => {
  const a = previewPrincipal("analyst");
  a.capabilities.workspace_execute_ml = true;
  assert.equal(previewPrincipal("analyst").capabilities.workspace_execute_ml, undefined);
});

test("the preview survives moving between screens and resets on a reload", () => {
  const storage = memoryStorage();
  storePreview(storage, "analyst");
  assert.equal(storage.data.get(VIEW_AS_STORAGE_KEY), "analyst");
  assert.equal(restorePreview(storage, "navigate"), "analyst");
  assert.equal(restorePreview(storage, "back_forward"), "analyst");
  assert.equal(restorePreview(storage, "reload"), null);
  assert.equal(storage.data.has(VIEW_AS_STORAGE_KEY), false);
  storage.setItem(VIEW_AS_STORAGE_KEY, "workspace_admin");
  assert.equal(restorePreview(storage, "navigate"), null);
  storePreview(storage, "business_user");
  storePreview(storage, null);
  assert.equal(storage.data.has(VIEW_AS_STORAGE_KEY), false);
  assert.equal(restorePreview(null, "navigate"), null);
  const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } };
  assert.equal(restorePreview(throwing, "navigate"), null);
  assert.doesNotThrow(() => storePreview(throwing, "analyst"));
});
