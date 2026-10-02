import assert from "node:assert/strict";
import test from "node:test";

import { NextRequest } from "next/server.js";

import { proxyBackend } from "./bff-proxy.ts";

test("BFF forwards the /v1 contract headers both ways and never Authorization", async () => {
  let seen: { url: string; init: RequestInit } | null = null;
  const fakeFetch = (async (url: string, init: RequestInit) => {
    seen = { url, init };
    return new Response("{}", {
      status: 201,
      headers: { "content-type": "application/json", etag: '"7"', "idempotent-replayed": "true" },
    });
  }) as unknown as typeof fetch;
  const req = new NextRequest("http://studio.test/api/backend/v1/service-tokens", {
    method: "POST",
    body: JSON.stringify({ name: "ci" }),
    headers: {
      "content-type": "application/json",
      "content-length": "13",
      "idempotency-key": "k-1",
      "if-match": '"6"',
      "if-none-match": "*",
      authorization: "Bearer dclab_st_x",
    },
  });
  const response = await proxyBackend(req, ["v1", "service-tokens"], fakeFetch);
  assert.ok(seen);
  const forwarded = new Headers((seen as { init: RequestInit }).init.headers);
  assert.equal(forwarded.get("idempotency-key"), "k-1");
  assert.equal(forwarded.get("if-match"), '"6"');
  assert.equal(forwarded.get("if-none-match"), "*");
  assert.equal(forwarded.get("content-length"), "13");
  assert.equal(forwarded.get("authorization"), null);
  assert.equal(response.status, 201);
  assert.equal(response.headers.get("etag"), '"7"');
  assert.equal(response.headers.get("idempotent-replayed"), "true");
});
