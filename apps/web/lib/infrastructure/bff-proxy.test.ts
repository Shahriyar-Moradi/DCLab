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

test("BFF streams assistant SSE unbuffered with no-transform and forwards Last-Event-ID", async () => {
  let seen: RequestInit | null = null;
  let release: () => void = () => {};
  const gate = new Promise<void>((resolve) => (release = resolve));
  const encode = (text: string) => new TextEncoder().encode(text);
  let pulls = 0;
  const upstream = new ReadableStream<Uint8Array>({
    async pull(controller) {
      pulls += 1;
      if (pulls === 1) return controller.enqueue(encode('event: turn_started\ndata: {"n":1}\n\n'));
      await gate; // the upstream ends only after the client read the first event
      controller.enqueue(encode('event: turn_done\ndata: {"n":2}\n\n'));
      controller.close();
    },
  });
  const fakeFetch = (async (_url: string, init: RequestInit) => {
    seen = init;
    return new Response(upstream, { status: 200, headers: { "content-type": "text/event-stream" } });
  }) as unknown as typeof fetch;
  const req = new NextRequest("http://studio.test/api/backend/v1/assistant/threads/t/messages", {
    method: "POST",
    body: JSON.stringify({ text: "hi" }),
    headers: { "content-type": "application/json", "content-length": "13", "last-event-id": "4" },
  });
  const response = await proxyBackend(req, ["v1", "assistant", "threads", "t", "messages"], fakeFetch);
  assert.equal(new Headers((seen as unknown as RequestInit).headers).get("last-event-id"), "4");
  assert.equal(response.headers.get("content-type"), "text/event-stream");
  assert.equal(response.headers.get("cache-control"), "no-cache, no-transform");
  assert.equal(response.headers.get("x-accel-buffering"), "no");
  const reader = response.body!.getReader();
  const timeout = new Promise<never>((_resolve, reject) => setTimeout(() => reject(new Error("buffered")), 2000));
  const first = await Promise.race([reader.read(), timeout]);
  assert.match(new TextDecoder().decode(first.value), /turn_started/);
  release();
  let rest = "";
  for (let chunk = await reader.read(); !chunk.done; chunk = await reader.read()) {
    rest += new TextDecoder().decode(chunk.value);
  }
  assert.match(rest, /turn_done/);
});
