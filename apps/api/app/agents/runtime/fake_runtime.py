"""The fake runtime: deterministic, scripted runs for tests and local development
(ADR 0009 §5.1 step 6, §9; P6.3-A).

A case is a recorded fixture ``<cases_root>/<agent_key>/<case>.json``: an ordered list
of steps (``llm``: one ``session.complete`` with the agent class's output schema;
``tool``: one ``session.call_tool``) and optionally a scripted final ``output`` (checked
against the schema), ``message`` and ``proposal``. Without a scripted output the run's
output is the last answered model call, mapped by the agent class (``to_output``).
Model calls still go through the harness and the gateway (in tests and development:
the gateway's fake provider), so budgets, events, the ledger and replay behave as for
a real runtime. Refused outside a development environment, like the fake provider
(``fake_provider_allowed`` on ``DCLAB_ENV``). Never imports ``nooa``.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agents.contracts import AgentRunSpec
from app.agents.governance.platform_default import fake_provider_allowed
from app.agents.runtime.base import AgentClass, ProposalDraft, RuntimeOutput, RuntimeRefused, agent_class, finish
from app.domain.agent_records import CODE_PATTERN, KEY_PATTERN

VERSION = "fake==1"
DEFAULT_CASE = "default"
DEFAULT_CASES_ROOT = Path(__file__).resolve().parent / "fake_cases"  # P6.4-A adds one per agent class
MAX_STEPS = 50
_CASE = re.compile(CODE_PATTERN)
_KEY = re.compile(KEY_PATTERN)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FakeStep(_Strict):
    kind: Literal["llm", "tool"]
    tool: str | None = Field(default=None, pattern=KEY_PATTERN)
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)
    max_output_tokens: int | None = Field(default=None, ge=1, le=32_000)


class FakeProposal(_Strict):
    proposal_type: str = Field(min_length=1, max_length=64)
    decision_point_key: str = Field(pattern=KEY_PATTERN)
    payload: dict[str, Any]
    answer_kind: str | None = Field(default=None, max_length=64)
    rationale: str | None = Field(default=None, max_length=4000)


class FakeCase(_Strict):
    schema_version: Literal[1]
    agent_key: str = Field(pattern=KEY_PATTERN)
    case: str = Field(pattern=CODE_PATTERN)
    steps: tuple[FakeStep, ...] = Field(max_length=MAX_STEPS)
    output: dict[str, Any] | None = None
    message: str | None = Field(default=None, max_length=4000)
    proposal: FakeProposal | None = None


def _require_development() -> None:
    from app.config import get_settings

    if not fake_provider_allowed(get_settings().dclab_env):
        raise RuntimeRefused("fake_runtime_forbidden")


def load_case(agent_key: str, case: str, root: Path) -> FakeCase:
    if not (_KEY.fullmatch(agent_key) and _CASE.fullmatch(case)):
        raise RuntimeRefused("fake_case_missing")
    base = root.resolve()
    path = (base / agent_key / f"{case}.json").resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise RuntimeRefused("fake_case_missing", f"{agent_key}/{case}")
    try:
        loaded = FakeCase.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, ValidationError):
        raise RuntimeRefused("fake_case_invalid", f"{agent_key}/{case}") from None
    if (loaded.agent_key, loaded.case) != (agent_key, case):
        raise RuntimeRefused("fake_case_invalid", f"{agent_key}/{case}")
    return loaded


class FakeRuntime:
    name = "fake"
    version = VERSION

    def __init__(self, item: AgentClass, script: FakeCase) -> None:
        self.agent_class, self.script = item, script
        self.output_schema = item.output_schema
        self.validate_output = item.validate_output
        self._used = False

    def run(self, session: Any) -> RuntimeOutput:
        if self._used:
            raise RuntimeRefused("runtime_reused")  # one instance per run
        self._used = True
        _require_development()
        last: BaseModel | None = None
        refusal: str | None = None
        for step in self.script.steps:
            if step.kind == "llm":
                response = session.complete(output_schema=self.output_schema,
                                            max_output_tokens=step.max_output_tokens
                                            or self.agent_class.max_output_tokens)
                if response.ok and response.output is not None:
                    last = response.output
                else:
                    refusal = response.refusal.code if response.refusal else "provider_error"
            else:
                session.call_tool(step.tool or "", step.arguments, reason=step.reason)
        if self.script.output is not None:
            try:
                last = self.output_schema.model_validate(self.script.output)
            except ValidationError:
                raise RuntimeRefused("fake_case_invalid", "scripted output") from None
        if last is None:
            return RuntimeOutput(meta={"refusal": refusal} if refusal else {})
        output = finish(self.agent_class, last)
        overrides: dict[str, Any] = {}
        if self.script.message is not None:
            overrides["message"] = self.script.message
        if self.script.proposal is not None:
            overrides["proposal"] = ProposalDraft(**self.script.proposal.model_dump())
        return replace(output, **overrides) if overrides else output


def factory(spec: AgentRunSpec, *, case: str = DEFAULT_CASE, cases_root: Path | None = None) -> FakeRuntime:
    """The registered ``fake`` factory (case ``default``); tests bind another case or root
    with ``functools.partial``."""

    _require_development()
    item = agent_class(spec.agent_key)
    return FakeRuntime(item, load_case(spec.agent_key, case, cases_root or DEFAULT_CASES_ROOT))
