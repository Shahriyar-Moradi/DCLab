import assert from "node:assert/strict";
import test from "node:test";

import {
  CAPABILITIES,
  canAccessProductRoute,
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
