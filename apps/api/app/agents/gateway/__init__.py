"""The AI gateway: the only door to any model (ADR 0009 §1, §4).

``service.GatewayService.complete`` (LLMs) and ``.decide`` (Jev) run the eleven
steps policy → switches → router → redaction → limits → budget → cache →
provider → validate → ledger → settle and return typed refusals, never
exceptions. Provider SDKs live only in ``providers/``.
"""
