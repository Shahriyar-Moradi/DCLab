import assert from "node:assert/strict";
import test from "node:test";

import { CLI_SHELL, MCP_JSON, SDK_PYTHON, TOKEN_PLACEHOLDER, containsRealToken } from "./studio-agents.ts";

test("the .mcp.json snippet is valid JSON with the dclab server and a placeholder token", () => {
  const parsed = JSON.parse(MCP_JSON) as { mcpServers: { dclab: { command: string; env: Record<string, string> } } };
  const server = parsed.mcpServers.dclab;
  assert.equal(server.command, "python");
  assert.equal(server.env.DCLAB_TOKEN, TOKEN_PLACEHOLDER);
  assert.ok("DCLAB_API_URL" in server.env);
  assert.equal(server.env.DCLAB_MCP_WRITE_ENABLED, "0", "write tools start off");
});

test("no snippet embeds a real token", () => {
  for (const text of [MCP_JSON, SDK_PYTHON, CLI_SHELL]) assert.equal(containsRealToken(text), false);
  assert.equal(containsRealToken("dclab_st_AbCdEf0123456789"), true);
  assert.equal(containsRealToken(TOKEN_PLACEHOLDER), false);
});
