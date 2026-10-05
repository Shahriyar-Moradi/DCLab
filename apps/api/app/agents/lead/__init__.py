"""The lead agent = the in-app assistant's backend (ADR 0008 §6; ADR 0009 §7; P6.3-B).

A harness runtime (``lead_loop``), not a parallel system: imported only by
``app.agents.harness`` (CI rule c, like ``app.agents.runtime``). Every step is one typed
gateway call returning an ``AssistantStep``; tools come from the one shared catalog (the
same definitions MCP exports) and run in agent consumer mode; write tools become L1
``ToolCallProposal`` rows and never act; bounds, budget, events and ledger are the
harness's. ``fake_driver`` scripts the lead model for tests and local development.
"""
