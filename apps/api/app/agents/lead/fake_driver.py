"""A scripted lead model for tests and local development (P6.3-B; P6.10-B builds on it).

``ScriptedLeadDriver`` is a ``FakeProvider`` handler (the gateway's fake provider, refused
outside an explicit development ``DCLAB_ENV``): each model call answers with the next
scripted ``AssistantStep`` — a dict, or a callable over the redacted request payload the
gateway built (e.g. to read a pending proposal id from the transcript). ``requests`` keeps
every payload, so a test can assert exactly what would have left the process.
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable, Iterable
from typing import Any

Step = dict[str, Any] | Callable[[dict[str, Any]], dict[str, Any]]


class ScriptedLeadDriver:
    def __init__(self, steps: Iterable[Step]) -> None:
        self.steps: deque[Step] = deque(steps)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, call: Any) -> dict[str, Any]:
        payload = json.loads(call.input_json)
        self.requests.append(payload)
        if not self.steps:
            raise AssertionError("the scripted lead model has no step left")
        step = self.steps.popleft()
        return dict(step(payload) if callable(step) else step)


def answer(message: str, *citations: tuple[str, Any]) -> dict[str, Any]:
    return {"kind": "answer", "message": message, "citations": [{"kind": k, "id": str(i)} for k, i in citations]}


def tools(*calls: tuple[str, dict[str, Any]]) -> dict[str, Any]:
    return {"kind": "tool_calls", "tool_calls": [{"tool": name, "arguments": args, "reason": "scripted"}
                                                 for name, args in calls]}
