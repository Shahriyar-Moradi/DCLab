import assert from "node:assert/strict";
import test from "node:test";

import { safeInternalHref } from "./safe-href.ts";

test("rejects script, scheme, protocol-relative, backslash and control-character hrefs", () => {
  for (const bad of [
    "javascript:alert(1)", " javascript:x", "\tjavascript:x", "JaVaScRiPt:1", "data:text/html,x", "https://x", "http://x",
    "//evil.example", "/\\evil", "\\\\evil", "/a\\b", "/%0d%0a", "/x%0Ay", "/%5cevil", "/a b", "/a\nb", "", "x/y", "ok",
    "/" + "a".repeat(2100),
  ]) {
    assert.equal(safeInternalHref(bad), null, JSON.stringify(bad));
  }
  for (const bad of [null, undefined, 1, {}, ["/ok"]]) assert.equal(safeInternalHref(bad), null);
});

test("accepts same-origin paths", () => {
  for (const ok of ["/", "/ok/path?x=1#y", "/projects/p1/graph", "/outcomes#predictions", "/a%20b"]) {
    assert.equal(safeInternalHref(ok), ok);
  }
});
