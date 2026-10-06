import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPABILITIES,
  canAccessProductRoute,
  studioRoute,
  defaultProductRoute,
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
  assert.equal(defaultProductRoute(platform), "/admin/businesses");
  assert.equal(canAccessProductRoute(platform, "/admin/organizations"), true);
  assert.equal(canAccessProductRoute(platform, "/business"), false);

  const noWorkspace = user({ [CAPABILITIES.accountAccess]: true });
  assert.equal(defaultProductRoute(noWorkspace), "/app/settings");
  assert.equal(canAccessProductRoute(noWorkspace, "/app/dashboards"), false);
});

test("Studio routes need the development workspace role", () => {
  const developer = user({ [CAPABILITIES.developmentAccess]: true });
  const client = user({ [CAPABILITIES.applicationAccess]: true });
  for (const path of ["/home", "/inbox", "/projects", "/projects/p1/experiments/e1"]) {
    assert.equal(canAccessProductRoute(developer, path), true, path);
    assert.equal(canAccessProductRoute(client, path), false, path);
  }
  assert.equal(canAccessProductRoute(developer, "/projectsx"), false);
  assert.equal(safeWorkspaceSwitchDestination(developer, "/projects"), "/projects");
  assert.equal(safeWorkspaceSwitchDestination(developer, "/projects/p1/graph"), "/development");
});

test("Studio paths: known sections and UUID ids only; a project opens on Experiments", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  for (const ok of ["/home", "/inbox", "/projects", "/projects/new", `/projects/${id}/experiments/new`, `/projects/${id}/experiments/compare`, `/projects/${id}/graph`, `/projects/${id}/experiments/${id}`,
    ...["data", "splits", "features", "models"].map((section) => `/projects/${id}/${section}/${id}`)]) {
    assert.equal(studioRoute(ok), "ok", ok);
  }
  assert.deepEqual(studioRoute(`/projects/${id}`), { redirect: `/projects/${id}/experiments` });
  for (const bad of ["/projects/x/graph", `/projects/${id}/nope`, `/projects/${id}/constructor`, `/projects/${id}/graph/x`,
    `/projects/${id}/experiments/x`, "/home/x", "/projects/AAAAAAAA-1111-4111-8111-111111111111/graph", "/Projects", `/projects/${id}%2Fgraph`, "/projects//evil.example", "/projects/new/x", "/projects/news", `/projects/${id}/experiments/newx`, `/projects/${id}/new`,
    `/projects/${id}/data/x`, `/projects/${id}/splits/${id}/x`, `/projects/${id}/features`, `/projects/${id}/graph/${id}`]) {
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
