import assert from "node:assert/strict";
import test from "node:test";

import { errorMessage } from "./api-error-message.ts";

test("legacy string detail and the /v1 envelope keep their message", () => {
  assert.equal(errorMessage({ detail: "not found" }), "not found");
  assert.equal(errorMessage({ error: { code: "quota_exceeded", message: "Limit reached." } }), "Limit reached.");
});

test("an object detail (legacy 429 quota_exceeded) shows its message, never [object Object]", () => {
  const body = { detail: { code: "quota_exceeded", message: "This workspace has reached its daily run limit." } };
  assert.equal(errorMessage(body), "This workspace has reached its daily run limit.");
  assert.equal(errorMessage({ detail: { code: "x" } }), null);
  assert.equal(errorMessage({ detail: [{ msg: "bad" }] }), null);
  assert.equal(errorMessage(null), null);
});
