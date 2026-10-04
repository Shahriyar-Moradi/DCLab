import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { contractOperations, coverageEntries, uncoveredOperations } from "./ui-coverage.ts";

const REPO_ROOT = new URL("../../../../", import.meta.url);
const contract = JSON.parse(readFileSync(new URL("contracts/v1_openapi.json", REPO_ROOT), "utf8"));
const coverage = readFileSync(new URL("docs/mvp/UI_COVERAGE.md", REPO_ROOT), "utf8");

test("every /v1 operation in the contract has a row in docs/mvp/UI_COVERAGE.md", () => {
  const operations = contractOperations(contract);
  assert.ok(operations.length > 0, "contracts/v1_openapi.json lists no operations");
  const missing = uncoveredOperations(operations, coverage);
  assert.deepEqual(
    missing,
    [],
    `Add a UI_COVERAGE.md row (Studio screen, planned prompt, or "API-only by design") for:\n  ${missing.join("\n  ")}`,
  );
});

test("a fake new operation added to the real contract is reported", () => {
  const real = contractOperations(contract);
  const baseline = uncoveredOperations(real, coverage);
  const fakes = [
    { method: "POST", path: "/v1/fake-widgets/{widget_id}/frobnicate" },
    // A new method on a path that already has rows is still a new operation.
    { method: "DELETE", path: "/v1/projects/{project_id}" },
  ];
  assert.deepEqual(uncoveredOperations([...real, ...fakes], coverage), [
    ...baseline,
    "POST /v1/fake-widgets/{widget_id}/frobnicate",
    "DELETE /v1/projects/{project_id}",
  ]);
});

test("matcher: placeholders, grouped methods, relative suffixes and alternatives", () => {
  const markdown = [
    "| Capability | Ops |",
    "| --- | --- |",
    "| Tokens | `GET/POST /v1/service-tokens`, `POST …/{id}/revoke` |",
    "| Builds | `GET /v1/model-builds/{id}`, `…/events`, `/artifacts` |",
    "| Refs | `GET /v1/projects/{id}/refs`, `GET/POST …/refs/{kind}` |",
    "| Decide | `POST /v1/decisions/{id}/accept|reject` |",
    "| Probe | `GET /health` |",
    "Outside a table: `GET /v1/not-a-row`",
  ].join("\n");
  const ops = (...pairs: [string, string][]) => pairs.map(([method, path]) => ({ method, path }));
  assert.deepEqual(
    uncoveredOperations(
      ops(
        ["GET", "/v1/service-tokens"],
        ["POST", "/v1/service-tokens"],
        ["POST", "/v1/service-tokens/{token_id}/revoke"],
        ["GET", "/v1/model-builds/{pipeline_run_id}/events"],
        ["GET", "/v1/model-builds/{pipeline_run_id}/artifacts"],
        ["POST", "/v1/projects/{project_id}/refs/{ref_kind}"],
        ["POST", "/v1/decisions/{decision_id}/reject"],
      ),
      markdown,
    ),
    [],
  );
  assert.deepEqual(
    uncoveredOperations(
      ops(
        ["DELETE", "/v1/service-tokens"],
        ["POST", "/v1/model-builds/{id}/events"],
        ["POST", "/v1/projects/{id}/refs"],
        ["POST", "/v1/decisions/{id}/supersede"],
        ["GET", "/v1/not-a-row"],
        ["GET", "/v1/other/{id}/events"],
      ),
      markdown,
    ),
    [
      "DELETE /v1/service-tokens",
      "POST /v1/model-builds/{id}/events",
      "POST /v1/projects/{id}/refs",
      "POST /v1/decisions/{id}/supersede",
      "GET /v1/not-a-row",
      "GET /v1/other/{id}/events",
    ],
  );
  assert.equal(coverageEntries("| x | `inspect_project` | `GET /health` |").length, 0);
});
