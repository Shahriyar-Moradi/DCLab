"""The offline AI test harness (P6.10-B; ADR 0009 §9). ``make test-ai`` runs this package
(marker ``ai_harness``) with deterministic fakes only: no network (``conftest.py`` refuses
any non-loopback socket), no provider SDK, no key; AI_ENABLED comes from each fixture's
settings lambda. The R3 runner (P6.8-A) builds on the API below.

Fake provider. Every model call goes through the real gateway with the gateway's
``FakeProvider``; its handler is a ``Scenario``:

* recorded fixtures ``tests/fixtures/ai/<agent>/<case>.json`` (``load_fixture``,
  ``fixture_cases``): a list of exchanges ``input_digest -> output``. The key
  (``call_key``) is computed from exactly what the gateway sent: the redacted payload
  (``input_json``, per-run ids normalised to ``<id:N>`` by ``Ids`` so a fixture survives a
  fresh database), the prompt release text, the output schema digest and the model. The
  ledger joins on the gateway's own cache key (``ProviderCall.input_digest`` =
  ``llm_invocations.input_evidence_digest``), which contains per-run workspace and
  release ids and so cannot key a portable fixture. A payload recorded under another
  prompt release, output schema or model raises ``FixtureRejected`` (never reused); an
  unknown payload raises ``FixtureMissing``; ``Scenario.finish()`` asserts neither
  happened and every exchange was used. Outputs store ids as placeholders and are
  filled back with the current run's ids. Each exchange also stores the payload's key
  paths (no values) so a re-record that adds a field shows in the diff.
  The key is deliberately loose in three ways: lists compare as multisets
  (``unordered=True``: services order some lists by id), ids are masked to their shape
  (``<id:N>`` by first appearance in an id-independent order), and the purpose / agent are
  not part of it (the instructions digest pins the prompt release instead).
* scripted scenarios: ``Scenario(..., script=handler)`` with ``answers(...)``,
  ``ScriptedLeadDriver`` (``app.agents.lead.fake_driver``) or a fake-Jev table.

Regenerate fixtures and goldens (deterministic; review the diff)::

    DCLAB_RECORD_AI=1 DCLAB_UPDATE_GOLDEN=1 make test-ai

Goldens: ``run_transcript`` (events, proposals, ledger rows of an agent run, normalised)
and ``assert_golden`` (``tests/golden/ai/<name>.json``; ``DCLAB_UPDATE_GOLDEN=1``).

Chaos: ``Chaos(kind, valid_script, flip=...)`` for ``CHAOS_KINDS`` — ``handler`` (timeout,
invalid output), ``trip`` (breaker open), ``wrap`` (kill switch flipped before gateway call
N), ``refuse_reservation`` (over budget); agent runs also take a narrow ``RunLimits`` hold.

Property checks (each raises ``AssertionError`` listing its findings; each has a planted-
violation meta-test in ``test_ai_properties.py``): ``check_codeact_ban`` /
``codeact_findings``, ``registry_findings`` (holdout-blind tool registry over the catalog,
its export, the committed contract and the MCP listing), ``check_no_raw_rows`` /
``raw_row_findings`` (request bodies), ``check_ledger`` / ``ledger_findings`` (one complete
``llm_invocations`` row per model call), ``check_harness_records`` / ``harness_findings``
(``agent_runs`` + ``agent_events`` for every run, refused and failed ones included).
"""

from ai_harness.kit import *  # noqa: F403  (``kit.__all__`` is the public API)
from ai_harness.kit import __all__  # noqa: F401
