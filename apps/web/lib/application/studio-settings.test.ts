import assert from "node:assert/strict";
import test from "node:test";

import { ADDING_PEOPLE_TEXT, MORE_SETTINGS_TEXT, formatDay, memberRow, membersCountText, myAccess, roleRow, roleWords } from "./studio-settings.ts";

test("roles have plain words; unknown and inherited names are shown as cleaned text", () => {
  assert.deepEqual(["workspace_owner", "workspace_admin", "ml_engineer", "viewer"].map(roleWords), ["Admin · Owner", "Admin", "Data scientist", "Analyst (view only)"]);
  for (const bad of ["constructor", "toString", "hasOwnProperty"]) assert.equal(roleWords(bad), bad);
  assert.equal(roleWords("__proto__"), "proto");
  assert.equal(roleWords(undefined), "Unknown role");
  assert.equal(roleWords("x\n\u0007y"), "x y");
});

test("a role row is built from the server flags; missing flags mean cannot", () => {
  const admin = roleRow({ role: "workspace_admin", can_read: true, can_run_ml: true, can_manage_members: true, can_approve_ai_policy: true });
  assert.equal(admin.cannot.length, 0);
  const viewer = roleRow({ role: "viewer", can_read: true, can_run_ml: false, can_manage_members: false, can_approve_ai_policy: false });
  assert.equal(viewer.label, "Analyst (view only)");
  assert.equal(viewer.can.length, 1);
  assert.equal(viewer.cannot.length, 3);
  const partial = roleRow({ role: "__proto__" } as never);
  assert.equal(partial.can.length, 0);
  assert.equal(partial.label, "proto");
});

test("a member row is plain text with a you marker and no invented fields", () => {
  const row = memberRow({ full_name: "  <b>Ada</b>\n", email: "ada@example.com", role: "workspace_admin", joined_at: "2026-01-02T03:04:05Z", is_you: true });
  assert.equal(row.name, "<b>Ada</b>"); // text, rendered by React as text
  assert.equal(row.role, "Admin");
  assert.equal(row.you, true);
  assert.deepEqual(Object.keys(row).sort(), ["email", "joined", "key", "legacy", "name", "role", "you"]);
  assert.equal(row.legacy, false);
  assert.equal(memberRow({ full_name: "", email: "e@x.y", role: "viewer", joined_at: "", is_you: false, legacy_access: true }).legacy, true);
  // A client account (listed as viewer with legacy_access) reads "Business user".
  assert.equal(memberRow({ full_name: "", email: "e@x.y", role: "viewer", joined_at: "", is_you: false, legacy_access: true }).role, "Business user");
  assert.equal(memberRow({ full_name: "", email: "e@x.y", role: "viewer", joined_at: "", is_you: false }).role, "Analyst (view only)");
  const long = `${"a".repeat(200)}@x.y`;
  assert.equal(memberRow({ full_name: "", email: long, role: "viewer", joined_at: "", is_you: false }).key, long);
  assert.equal(memberRow({ full_name: "", email: "e@x.y", role: "viewer", joined_at: "nope", is_you: false }).name, "No name given");
  assert.equal(formatDay("nope"), "date unknown");
  assert.equal(memberRow({ full_name: "a".repeat(500), email: "e@x.y", role: "viewer", joined_at: "", is_you: false }).name.length, 80);
});

test("my access comes from own capability keys only", () => {
  assert.equal(myAccess(undefined).canSeeMembers, false);
  assert.equal(myAccess({ workspace_manage_members: true }).canSeeMembers, true);
  assert.equal(myAccess({ workspace_manage_members: false, workspace_read: true }).canSeeMembers, false);
  assert.equal(myAccess(JSON.parse('{"__proto__": {"workspace_manage_members": true}}')).canSeeMembers, false);
  assert.match(myAccess({}).sentences.join(" "), /cannot change work/);
});

test("the honest fixed lines and the count sentence", () => {
  assert.match(ADDING_PEOPLE_TEXT, /not available in Studio yet; contact DCLab/);
  assert.match(MORE_SETTINGS_TEXT, /later releases/);
  assert.equal(membersCountText(1, false), "1 person");
  assert.equal(membersCountText(200, true), "Showing the first 200 people; there are more.");
});
