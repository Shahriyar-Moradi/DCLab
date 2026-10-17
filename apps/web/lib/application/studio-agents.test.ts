import assert from "node:assert/strict";
import test from "node:test";

import { ABILITY_SCOPES, CLI_SHELL, MCP_JSON, SDK_PYTHON, TOKEN_LIMIT_TEXT, TOKEN_PLACEHOLDER, V1_CONVENTIONS, WRITE_SWITCH_TEXT, abilityHelp, abilityLabel, containsRealToken } from "./studio-agents.ts";

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

test("every permission has plain words; an unknown one is shown cleaned and inherited names get none", () => {
  assert.deepEqual(ABILITY_SCOPES.map(abilityLabel), ["Read", "Create projects and goals", "Upload data", "Run experiments and scoring", "Propose decisions"]);
  for (const part of [/score data with a model/, /target question/, /AI agent reviews/, /AI budget/]) assert.match(abilityHelp("experiments:write")!, part);
  assert.match(abilityHelp("read")!, /prediction files/);
  assert.equal(abilityLabel("future:scope"), "future:scope");
  for (const key of ["__proto__", "constructor", "toString"]) { assert.equal(abilityHelp(key), null, key); assert.notEqual(abilityLabel(key), undefined); }
});

test("the Connect page claims no more than is enforced", () => {
  const text = [TOKEN_LIMIT_TEXT, WRITE_SWITCH_TEXT, ...V1_CONVENTIONS.map((c) => c.value), SDK_PYTHON, CLI_SHELL].join("\n");
  assert.doesNotMatch(text, /service token|scope|governance|raw rows|never contain/i);
  assert.match(TOKEN_LIMIT_TEXT, /cannot accept a decision or choose a model for use/);
  assert.match(CLI_SHELL, /dclab-cli predict create/);
  assert.match(SDK_PYTHON, /api\.predictions\.create/);
});
