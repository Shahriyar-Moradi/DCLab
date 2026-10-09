import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPABILITIES,
  canAccessProductRoute,
  studioRoute,
  defaultProductRoute,
  isLegacyMarketingPath,
  safeNextPath,
  safeWorkspaceSwitchDestination,
} from "./capabilities.ts";
import {
  abortWorkspaceRequests,
  getWorkspaceRequestSignal,
  setActiveWorkspaceId,
  workspaceQueryKey,
  workspaceSelectorPresentation,
} from "./active-workspace.ts";
import type { SessionUser } from "./session.ts";

function user(capabilities: Record<string, boolean>): SessionUser {
  return {
    id: "user-1",
    email: "person@example.invalid",
    role: "viewer",
    full_name: "Person",
    workspace_id: null,
    active_workspace_id: "workspace-a",
    workspaces: [],
    capability_matrix_version: "workspace-capabilities.v1",
    capabilities,
  };
}

test("navigation follows server capabilities rather than the display role", () => {
  const platform = user({
    [CAPABILITIES.accountAccess]: true,
    [CAPABILITIES.platformRead]: true,
  });
  assert.equal(defaultProductRoute(platform), "/admin/monitoring");
  assert.equal(canAccessProductRoute(platform, "/admin/organizations"), true);
  assert.equal(canAccessProductRoute(platform, "/business"), false);

  const noWorkspace = user({ [CAPABILITIES.accountAccess]: true });
  assert.equal(defaultProductRoute(noWorkspace), "/app/settings");
  assert.equal(canAccessProductRoute(noWorkspace, "/app/dashboards"), false);
});

test("Studio routes need the development workspace role", () => {
  const developer = user({ [CAPABILITIES.developmentAccess]: true });
  const client = user({ [CAPABILITIES.applicationAccess]: true });
  for (const path of ["/home", "/inbox", "/agents", "/projects", "/projects/p1/experiments/e1"]) {
    assert.equal(canAccessProductRoute(developer, path), true, path);
    assert.equal(canAccessProductRoute(client, path), false, path);
  }
  assert.equal(canAccessProductRoute(developer, "/projectsx"), false);
  assert.equal(safeWorkspaceSwitchDestination(developer, "/projects"), "/projects");
  assert.equal(safeWorkspaceSwitchDestination(developer, "/projects/p1/graph"), "/projects");
});

test("Studio paths: known sections and UUID ids only; a project opens on Experiments", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  for (const ok of ["/home", "/inbox", "/agents", "/projects", "/projects/new", `/projects/${id}/predictions`, `/projects/${id}/experiments/new`, `/projects/${id}/experiments/compare`, `/projects/${id}/graph`, `/projects/${id}/experiments/${id}`,
    ...["data", "splits", "features", "models", "pipeline"].map((section) => `/projects/${id}/${section}/${id}`)]) {
    assert.equal(studioRoute(ok), "ok", ok);
  }
  assert.deepEqual(studioRoute(`/projects/${id}`), { redirect: `/projects/${id}/experiments` });
  for (const bad of ["/projects/x/graph", `/projects/${id}/nope`, `/projects/${id}/constructor`, `/projects/${id}/graph/x`,
    `/projects/${id}/experiments/x`, "/home/x", "/agents/x", "/agents/", "/projects/AAAAAAAA-1111-4111-8111-111111111111/graph", "/Projects", `/projects/${id}%2Fgraph`, "/projects//evil.example", "/projects/new/x", "/projects/news", `/projects/${id}/experiments/newx`, `/projects/${id}/new`,
    `/projects/${id}/predictions/x`, `/projects/${id}/predictions/${id}`, `/projects/${id}/data/x`, `/projects/${id}/pipeline/x`, `/projects/${id}/pipeline/${id}/x`, `/projects/${id}/splits/${id}/x`, `/projects/${id}/features`, `/projects/${id}/graph/${id}`]) {
    assert.equal(studioRoute(bad), "not_found", bad);
  }
});

test("the Decision.ai landing page needs the legacy decision layer flag", () => {
  const app = { [CAPABILITIES.accountAccess]: true, [CAPABILITIES.applicationAccess]: true };
  assert.equal(defaultProductRoute(user(app)), "/app/labs");
  assert.equal(defaultProductRoute(user({ ...app, [CAPABILITIES.legacyDecisionLayer]: true })), "/app/dashboards");
});

test("workspace switches preserve only capability-safe collection routes", () => {
  const business = user({
    [CAPABILITIES.accountAccess]: true,
    [CAPABILITIES.applicationAccess]: true,
    [CAPABILITIES.businessAccess]: true,
  });
  assert.equal(
    safeWorkspaceSwitchDestination(business, "/app/opportunities"),
    "/app/opportunities",
  );
  assert.equal(
    safeWorkspaceSwitchDestination(business, "/app/opportunities/old-object"),
    "/business",
  );
});

test("zero, one and many workspace selector states are deterministic", () => {
  assert.deepEqual(workspaceSelectorPresentation([], null), {
    empty: true,
    label: "No workspace",
  });
  assert.deepEqual(
    workspaceSelectorPresentation([{ id: "a", name: "Alpha" }], "a"),
    { empty: false, label: "Alpha" },
  );
  assert.deepEqual(
    workspaceSelectorPresentation(
      [
        { id: "a", name: "Alpha" },
        { id: "b", name: "Beta" },
      ],
      "b",
    ),
    { empty: false, label: "Beta" },
  );
});

test("workspace context keys caches and aborts the preceding request generation", () => {
  setActiveWorkspaceId("workspace-a");
  const previous = getWorkspaceRequestSignal();
  assert.deepEqual(workspaceQueryKey("projects"), ["ws", "workspace-a", "projects"]);
  setActiveWorkspaceId("workspace-b");
  assert.equal(previous.aborted, true);
  assert.deepEqual(workspaceQueryKey("projects"), ["ws", "workspace-b", "projects"]);

  const current = getWorkspaceRequestSignal();
  abortWorkspaceRequests();
  assert.equal(current.aborted, true);
});

test("the Studio design kit route needs the development capability, not a prefix match", () => {
  const dev = user({ [CAPABILITIES.developmentAccess]: true });
  const client = user({ [CAPABILITIES.applicationAccess]: true, [CAPABILITIES.businessAccess]: true });
  assert.equal(canAccessProductRoute(dev, "/dev/studio-kit"), true);
  assert.equal(canAccessProductRoute(client, "/dev/studio-kit"), false);
  assert.equal(canAccessProductRoute(null, "/dev/studio-kit"), false);
  assert.equal(canAccessProductRoute(client, "/device"), false);
});

test("every role lands in its workspace after login", () => {
  const base = { [CAPABILITIES.accountAccess]: true };
  const admin = user({ ...base, [CAPABILITIES.platformRead]: true, [CAPABILITIES.developmentAccess]: true, [CAPABILITIES.applicationAccess]: true });
  const developer = user({ ...base, [CAPABILITIES.developmentAccess]: true, [CAPABILITIES.applicationAccess]: true });
  const operator = user({ ...base, [CAPABILITIES.platformRead]: true });
  const client = user({ ...base, [CAPABILITIES.applicationAccess]: true });
  const businessAdmin = user({ ...base, [CAPABILITIES.businessAccess]: true, [CAPABILITIES.developmentAccess]: true, [CAPABILITIES.applicationAccess]: true });
  assert.equal(defaultProductRoute(admin), "/projects");
  assert.equal(defaultProductRoute(developer), "/projects");
  assert.equal(defaultProductRoute(operator), "/admin/monitoring");
  assert.equal(defaultProductRoute(client), "/app/labs");
  assert.equal(defaultProductRoute(businessAdmin), "/projects");
  assert.equal(defaultProductRoute(user({ ...base, [CAPABILITIES.businessAccess]: true, [CAPABILITIES.applicationAccess]: true })), "/business");
  assert.equal(defaultProductRoute(user(base)), "/app/settings");
  assert.equal(defaultProductRoute(null), "/app/settings");
  for (const [who, route] of [[admin, "/projects"], [developer, "/projects"], [operator, "/admin/monitoring"], [client, "/app/labs"]] as const) {
    assert.equal(canAccessProductRoute(who, route), true, route);
  }
});

test("next= is honored only for same-origin product routes the user may open", () => {
  const developer = user({ [CAPABILITIES.developmentAccess]: true, [CAPABILITIES.applicationAccess]: true });
  assert.equal(safeNextPath(developer, "/projects"), "/projects");
  assert.equal(safeNextPath(developer, "/projects/new?x=1"), "/projects/new?x=1");
  assert.equal(safeNextPath(developer, "/app/labs"), "/app/labs");
  for (const bad of [
    null, undefined, "", "https://evil.example/projects", "//evil.example", "///evil.example", "/\\evil.example",
    "/app/..//evil.example", "/app/../admin", "/projects/%2e%2e/x", "javascript:alert(1)", "projects", "/login", "/", "/admin/monitoring",
    "/app/labs\nSet-Cookie: x=1", "/app/labs#//evil.example", "/home@evil.example", "/app/" + "a".repeat(600),
  ]) {
    assert.equal(safeNextPath(developer, bad as string | null | undefined), null, String(bad));
  }
});

test("only the frozen marketing paths are gated by the marketing flag", () => {
  for (const path of ["/industries", "/solutions", "/pricing", "/showcase", "/platform", "/company", "/resources", "/pricing/x"]) {
    assert.equal(isLegacyMarketingPath(path), true, path);
  }
  for (const path of ["/", "/login", "/business", "/projects", "/platformx", "/app/labs"]) {
    assert.equal(isLegacyMarketingPath(path), false, path);
  }
});
