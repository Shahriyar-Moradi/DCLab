import assert from "node:assert/strict";
import test from "node:test";

async function policyFor(mode) {
  process.env.NODE_ENV = mode;
  const { default: config } = await import(`./next.config.mjs?mode=${mode}`);
  const routes = await config.headers();
  return routes[0].headers.find((header) => header.key === "Content-Security-Policy").value;
}

test("React Refresh works in development without weakening production CSP", async () => {
  const previous = process.env.NODE_ENV;
  try {
    const development = await policyFor("development");
    assert.match(development, /script-src[^;]*'unsafe-eval'/);
    assert.match(development, /connect-src[^;]*ws: wss:/);

    const production = await policyFor("production");
    assert.doesNotMatch(production, /'unsafe-eval'/);
    assert.doesNotMatch(production, /connect-src[^;]*ws:|connect-src[^;]*wss:/);
    assert.match(production, /font-src 'self'/);
  } finally {
    if (previous === undefined) delete process.env.NODE_ENV;
    else process.env.NODE_ENV = previous;
  }
});
