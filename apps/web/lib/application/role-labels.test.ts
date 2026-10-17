import assert from "node:assert/strict";
import test from "node:test";

import { SESSION_ROLES, roleLabel as sessionRoleLabel } from "../infrastructure/session.ts";
import { ROLE_VALUES, accountRoleLabel, roleLabel, roleLabelWithDetail, roleWordsFor } from "./role-labels.ts";
import { roleWords } from "./studio-settings.ts";

/** Workspace membership values the API stores (WorkspaceRole in apps/api/app/db/models.py). */
const WORKSPACE_ROLES = ["workspace_owner", "workspace_admin", "ml_engineer", "viewer", "business_admin", "business_developer", "personal_developer"];

test("every role value the API can send has a label, and no label shows a raw role code", () => {
  for (const role of new Set([...ROLE_VALUES, ...SESSION_ROLES, ...WORKSPACE_ROLES])) {
    const words = roleWordsFor(role);
    assert.ok(words, `no label for ${role}`);
    for (const text of [roleLabel(role), roleLabelWithDetail(role), sessionRoleLabel(role as never), roleWords(role)]) {
      assert.doesNotMatch(text, /_/, `${role} shows a code: ${text}`);
      assert.notEqual(text.toLowerCase(), role, `${role} is shown raw`);
    }
  }
});

test("the founder's names: Admin, Data scientist, Analyst (view only), Business user", () => {
  assert.equal(roleLabel("workspace_owner"), "Admin");
  assert.equal(roleLabel("workspace_admin"), "Admin");
  assert.equal(roleLabelWithDetail("workspace_owner"), "Admin · Owner");
  assert.equal(roleLabelWithDetail("workspace_admin"), "Admin");
  assert.equal(roleLabel("ml_engineer"), "Data scientist");
  assert.equal(roleLabel("viewer"), "Analyst (view only)");
  assert.equal(roleLabel("client_user"), "Business user");
  // Legacy values read as the role the server translates them to.
  assert.equal(roleLabel("business_admin"), "Admin");
  assert.equal(roleLabel("business_developer"), "Analyst (view only)");
  assert.equal(roleLabel("personal_developer"), "Data scientist");
  assert.equal(sessionRoleLabel("dclab_admin"), "Platform admin · DCLab staff");
});

test("unknown or hostile values are cleaned text, never looked up through the prototype", () => {
  assert.equal(roleLabel("__proto__"), "proto");
  assert.equal(roleLabel("constructor"), "constructor");
  assert.equal(roleLabel("new_role"), "new role");
  assert.equal(roleLabel(undefined), "Unknown role");
  assert.equal(roleLabel(""), "Unknown role");
  assert.equal(roleLabel("x\n\u0007y"), "x y");
  assert.equal(roleLabel("a".repeat(80)).length, 40);
});

test("the account's own role: a client account is a Business user whatever its membership row stores", () => {
  assert.equal(accountRoleLabel({ role: "client_user" }, { role: "business_admin" }), "Business user");
  assert.equal(accountRoleLabel({ role: "client_user" }, null), "Business user");
  assert.equal(accountRoleLabel({ role: "business_admin" }, { role: "business_admin" }), "Admin");
  assert.equal(accountRoleLabel({ role: "dclab_admin" }, { role: null }), "Platform admin · DCLab staff");
  assert.equal(accountRoleLabel({ role: "workspace_admin" }, { role: "workspace_owner" }), "Admin · Owner");
  assert.equal(accountRoleLabel(null, null), "Unknown role");
});

test("screens that list roles pass them through the label map (no raw role cell)", async () => {
  const { readFileSync } = await import("node:fs");
  const files = [
    "app/components/explorer/WorkspaceList.tsx",
    "app/components/explorer/tables.tsx",
    "app/admin/organizations/[id]/page.tsx",
    "app/components/studio-app/StudioFrame.tsx",
    "app/components/business-view/BusinessFrame.tsx",
  ];
  for (const file of files) {
    const source = readFileSync(new URL(`../../${file}`, import.meta.url), "utf8");
    assert.doesNotMatch(source, /cell: \((row|user|member)\) => \1\.role \}/, `${file} shows a raw role`);
    assert.match(source, /roleLabelWithDetail|accountRoleLabel/, `${file} does not use the label map`);
  }
});
