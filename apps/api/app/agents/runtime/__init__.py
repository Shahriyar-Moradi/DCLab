"""Agent runtimes plug into the harness (ADR 0009 §1, §5). Imported only by
``app.agents.harness`` (CI rule c): ``fake_runtime`` (development / tests) and
``nooa_runtime`` (NOOA Predict, optional dependency) since P6.3-A; the lead loop (``lead_loop``)
lives in ``app.agents.lead`` (P6.3-B) under the same rule."""
