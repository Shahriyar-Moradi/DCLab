import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  CAPABILITIES,
  canAccessProductRoute,
  studioRoute,
  defaultProductRoute,
  isBusinessViewPath,
  isLegacyMarketingPath,
  isOperatorPath,
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
  for (const path of ["/home", "/inbox", "/agents", "/real", "/projects", "/projects/p1/experiments/e1"]) {
    assert.equal(canAccessProductRoute(developer, path), true, path);
    assert.equal(canAccessProductRoute(client, path), false, path);
  }
  assert.equal(canAccessProductRoute(developer, "/projectsx"), false);
  assert.equal(safeWorkspaceSwitchDestination(developer, "/projects"), "/projects");
  assert.equal(safeWorkspaceSwitchDestination(developer, "/projects/p1/graph"), "/projects");
});

test("Studio paths: known sections and UUID ids only; a project opens on Experiments", () => {
  const id = "11111111-1111-4111-8111-111111111111";
  for (const ok of ["/home", "/inbox", "/agents", "/settings", "/real", "/projects", "/projects/new", `/projects/${id}/predictions`, `/projects/${id}/experiments/new`, `/projects/${id}/experiments/compare`, `/projects/${id}/graph`, `/projects/${id}/experiments/${id}`,
    ...["data", "splits", "features", "models", "pipeline"].map((section) => `/projects/${id}/${section}/${id}`)]) {
    assert.equal(studioRoute(ok), "ok", ok);
  }
  assert.deepEqual(studioRoute(`/projects/${id}`), { redirect: `/projects/${id}/experiments` });
  for (const bad of ["/projects/x/graph", `/projects/${id}/nope`, `/projects/${id}/constructor`, `/projects/${id}/graph/x`,
    `/projects/${id}/experiments/x`, "/home/x", "/agents/x", "/agents/", "/settings/x", "/settings/", "/real/x", "/realx", "/projects/AAAAAAAA-1111-4111-8111-111111111111/graph", "/Projects", `/projects/${id}%2Fgraph`, "/projects//evil.example", "/projects/new/x", "/projects/news", `/projects/${id}/experiments/newx`, `/projects/${id}/new`,
    `/projects/${id}/predictions/x`, `/projects/${id}/predictions/${id}`, `/projects/${id}/data/x`, `/projects/${id}/pipeline/x`, `/projects/${id}/pipeline/${id}/x`, `/projects/${id}/splits/${id}/x`, `/projects/${id}/features`, `/projects/${id}/graph/${id}`]) {
    assert.equal(studioRoute(bad), "not_found", bad);
  }
});

test("business users land on the Business view; their Labs and legacy pages stay reachable", () => {
  const app = { [CAPABILITIES.accountAccess]: true, [CAPABILITIES.applicationAccess]: true };
  assert.equal(defaultProductRoute(user(app)), "/outcomes");
  const legacy = user({ ...app, [CAPABILITIES.legacyDecisionLayer]: true });
  assert.equal(defaultProductRoute(legacy), "/outcomes");
  for (const route of ["/app/labs", "/app/dashboards"]) assert.equal(canAccessProductRoute(legacy, route), true, route);
});

test("P7.6-A: the Business view is for workspace readers; business users never reach developer routes", () => {
  const client = user({ [CAPABILITIES.accountAccess]: true, [CAPABILITIES.applicationAccess]: true });
  const developer = user({ [CAPABILITIES.developmentAccess]: true, [CAPABILITIES.applicationAccess]: true });
  assert.equal(isBusinessViewPath("/outcomes"), true);
  for (const path of ["/outcomes/x", "/outcomes/", "/outcomesx", "/Outcomes"]) assert.equal(isBusinessViewPath(path), false, path);
  assert.equal(canAccessProductRoute(client, "/outcomes"), true);
  assert.equal(canAccessProductRoute(developer, "/outcomes"), true);
  assert.equal(canAccessProductRoute(user({ [CAPABILITIES.accountAccess]: true }), "/outcomes"), false);
  assert.equal(canAccessProductRoute(null, "/outcomes"), false);
  assert.equal(canAccessProductRoute(client, "/outcomes/x"), false);
  for (const path of ["/projects", "/home", "/inbox", "/settings", "/agents", "/projects/new", "/dev/studio-kit", "/development"]) {
    assert.equal(canAccessProductRoute(client, path), false, path);
  }
  assert.equal(safeNextPath(client, "/outcomes"), "/outcomes");
  assert.equal(safeNextPath(client, "/projects"), null);
  assert.equal(safeWorkspaceSwitchDestination(client, "/outcomes"), "/outcomes");
  assert.equal(safeWorkspaceSwitchDestination(client, "/projects"), "/outcomes");
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
  assert.equal(defaultProductRoute(client), "/outcomes");
  assert.equal(defaultProductRoute(businessAdmin), "/projects");
  assert.equal(defaultProductRoute(user({ ...base, [CAPABILITIES.businessAccess]: true, [CAPABILITIES.applicationAccess]: true })), "/business");
  assert.equal(defaultProductRoute(user(base)), "/app/settings");
  assert.equal(defaultProductRoute(null), "/app/settings");
  for (const [who, route] of [[admin, "/projects"], [developer, "/projects"], [operator, "/admin/monitoring"], [client, "/outcomes"]] as const) {
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

test("P8.5-UI: the operator console is for platform administrators only; they land there without a workspace", () => {
  const base = { [CAPABILITIES.accountAccess]: true };
  const platformAdmin = user({ ...base, [CAPABILITIES.platformRead]: true, [CAPABILITIES.platformWrite]: true });
  const platformDeveloper = user({ ...base, [CAPABILITIES.platformRead]: true });
  const adminInWorkspace = user({ ...base, [CAPABILITIES.platformRead]: true, [CAPABILITIES.platformWrite]: true, [CAPABILITIES.developmentAccess]: true });
  const owner = user({ ...base, [CAPABILITIES.workspaceRead]: true, [CAPABILITIES.workspaceWrite]: true, [CAPABILITIES.workspaceManageMembers]: true, [CAPABILITIES.workspaceExecuteMl]: true, [CAPABILITIES.developmentAccess]: true, [CAPABILITIES.applicationAccess]: true, [CAPABILITIES.businessAccess]: true });
  const viewer = user({ ...base, [CAPABILITIES.workspaceRead]: true, [CAPABILITIES.applicationAccess]: true });
  const client = user({ ...base, [CAPABILITIES.applicationAccess]: true });
  // A write capability alone (no platform read) is not enough either.
  const writeOnly = user({ ...base, [CAPABILITIES.platformWrite]: true });

  assert.equal(defaultProductRoute(platformAdmin), "/operator");
  assert.equal(defaultProductRoute(platformDeveloper), "/admin/monitoring");
  assert.equal(defaultProductRoute(adminInWorkspace), "/projects");
  assert.equal(canAccessProductRoute(platformAdmin, "/operator"), true);
  assert.equal(canAccessProductRoute(adminInWorkspace, "/operator"), true);
  for (const who of [platformDeveloper, owner, viewer, client, writeOnly, user(base), null]) {
    assert.equal(canAccessProductRoute(who, "/operator"), false);
    assert.equal(safeNextPath(who, "/operator"), null);
  }
  // The old monitoring page stays reachable for platform staff.
  assert.equal(canAccessProductRoute(platformAdmin, "/admin/monitoring"), true);
  assert.equal(canAccessProductRoute(platformDeveloper, "/admin/monitoring"), true);
  assert.equal(safeNextPath(platformAdmin, "/operator"), "/operator");
  assert.equal(safeNextPath(platformAdmin, "/operator?tab=workspaces"), "/operator?tab=workspaces");
  for (const path of ["/operator", "/operator/x"]) assert.equal(isOperatorPath(path), true, path);
  for (const path of ["/operatorx", "/Operator", "/operators", "/admin/operator"]) assert.equal(isOperatorPath(path), false, path);
  assert.equal(canAccessProductRoute(platformDeveloper, "/operator/x"), false);
  assert.equal(safeWorkspaceSwitchDestination(platformAdmin, "/operator"), "/operator");
});

test("P7.6-A: the middleware runs on every gated Studio and Business view route", () => {
  const source = readFileSync(new URL("../../middleware.ts", import.meta.url), "utf8");
  for (const prefix of ["/home", "/inbox", "/agents", "/settings", "/projects", "/outcomes", "/operator", "/dev", "/app", "/business"]) {
    assert.ok(source.includes(`"${prefix}/:path*"`), prefix);
  }
});

