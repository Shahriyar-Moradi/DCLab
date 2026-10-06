import assert from "node:assert/strict";
import test from "node:test";

import { CLIENT_PROCESSING_TITLE, labRunVisibility, processingTitle } from "./lab-run-view.ts";

test("non-development roles see no technical panel and no live stage text", () => {
  const view = labRunVisibility(false);
  assert.deepEqual(view, { technical: false, liveSteps: false });
  assert.equal(processingTitle(view, "Fitting gradient boosting"), "Analyzing your data");
  assert.equal(processingTitle(view, undefined), CLIENT_PROCESSING_TITLE);
});

test("development roles keep the technical panel and the live milestone", () => {
  const view = labRunVisibility(true);
  assert.deepEqual(view, { technical: true, liveSteps: true });
  assert.equal(processingTitle(view, "Fitting"), "Fitting");
});
