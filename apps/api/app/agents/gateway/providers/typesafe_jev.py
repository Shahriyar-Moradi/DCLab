"""TypeSafe Jev adapter: ``system_one`` through ``typesafe-sdk==0.7.2`` (ADR 0008 §5; ADR 0009 §9).

The only DCLab module that imports ``typesafe_sdk`` (the optional ``agents`` extra), and
only lazily: without it ``ready()`` is false and the semantic port stays deterministic.
The key is read only from ``DCLAB_TYPESAFE_API_KEY`` and passed explicitly, as are the
base URL (the SDK's default, never ``TYPESAFE_BASE_URL``) and the model (the release's
pinned id, never the SDK default ``jev-latest``). One request per call: SDK retries off
(the gateway owns retries; Jev gets none), the gateway's timeout per call. The SDK
logger is held at WARNING (its DEBUG lines carry request and response bodies).

Wire mapping: questions are named ``q0 … qN`` (never column names); each question's
instructions are the release's question text plus the state key it is about; the
state is the gateway's redacted state (column names wrapped as untrusted text).
Answers come back as ``{"value": …}``: P(yes) for noul, the label for choice, the
expected score for score.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from importlib.util import find_spec
from typing import Any

from app.agents.gateway.providers import ProviderCall, ProviderError, ProviderResult

API_KEY_ENV = "DCLAB_TYPESAFE_API_KEY"
SDK_PIN = "0.7.2"


def sdk_installed() -> bool:
    try:
        return find_spec("typesafe_sdk") is not None
    except (ImportError, ValueError):  # a module set to None / without a spec
        return False


def _sdk() -> Any:
    try:
        import typesafe_sdk
    except ImportError as exc:
        raise ProviderError("not_configured", "typesafe-sdk is not installed (agents extra)") from exc
    if typesafe_sdk.__version__ != SDK_PIN:
        raise ProviderError("not_configured", f"typesafe-sdk {typesafe_sdk.__version__} is not the pinned {SDK_PIN}")
    logging.getLogger("typesafe_sdk").setLevel(logging.WARNING)
    return typesafe_sdk


def _question(sdk: Any, spec: dict[str, Any], item: dict[str, Any]) -> Any:
    instructions = {"question": spec["question"], "about": item.get("subject") or "state"}
    criteria = spec.get("criteria")
    if item["primitive"] == "noul":
        return sdk.Noul(instructions=instructions, criteria=criteria)
    if item["primitive"] == "choice":
        labels = criteria if isinstance(criteria, dict) else {}
        return sdk.Choice(instructions=instructions, criteria={label: labels.get(label) for label in item["choices"]})
    return sdk.Score(instructions=instructions, criteria=list(criteria or ()))


def _answer(item: dict[str, Any], answer: Any) -> dict[str, Any]:
    key = item["question_key"]["untrusted_text"]
    if item["primitive"] == "noul":
        p = float(answer.noul)
        return {"question_key": key, "answer": {"value": p}, "probabilities": {"yes": p, "no": 1 - p}}
    if item["primitive"] == "choice":
        return {"question_key": key, "answer": {"value": answer.choice}, "confidence": float(answer.confidence),
                "probabilities": {str(k): float(v) for k, v in answer.probabilities.items()}}
    return {"question_key": key, "answer": {"value": float(answer.score)}, "confidence": float(answer.confidence),
            "probabilities": {str(k): float(v) for k, v in answer.probabilities.items()}}


class TypeSafeJevProvider:
    name = "typesafe"

    def __init__(self, *, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory

    def ready(self) -> bool:
        """The extra is installed and the key is set (else the port never calls)."""
        return sdk_installed() and bool(os.environ.get(API_KEY_ENV, "").strip())

    def _client(self, sdk: Any, model: str, timeout_s: float) -> Any:
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            raise ProviderError("not_configured", f"{API_KEY_ENV} is not set")
        options = dict(api_key=api_key, model=model, timeout=timeout_s, base_url=sdk.constants.DEFAULT_BASE_URL,
                       retry=sdk.RetryPolicy(max_retries=0, timeout=timeout_s))
        if self._client_factory is not None:
            return self._client_factory(**options)
        import httpx2  # the SDK's HTTP client; no proxy / CA / netrc settings from the environment

        return sdk.TypeSafeClient(**options, http_client=httpx2.Client(trust_env=False, timeout=timeout_s))

    def complete(self, call: ProviderCall) -> ProviderResult:
        sdk = _sdk()
        payload, spec = json.loads(call.input_json), json.loads(call.instructions)
        items = payload["questions"]
        state = payload["state"] if not payload.get("user_text") else {
            "state": payload["state"], "user_text": payload["user_text"]}
        questions = {f"q{index}": _question(sdk, spec, item) for index, item in enumerate(items)}
        client = self._client(sdk, call.model, call.timeout_s)
        try:
            with client:
                response = client.system_one(state=state, questions=questions, model=call.model)
        except sdk.TypeSafeAPITimeoutError as exc:
            raise ProviderError("timeout") from exc
        except sdk.TypeSafeAPIConnectionError as exc:
            raise ProviderError("server_error", "connection failed") from exc
        except sdk.TypeSafeRateLimitError as exc:
            raise ProviderError("rate_limited") from exc
        except sdk.TypeSafeAPIResponseValidationError as exc:
            raise ProviderError("invalid_output", "response did not validate") from exc
        except sdk.TypeSafeAPIError as exc:
            kind = "server_error" if exc.status >= 500 else "client_error"
            raise ProviderError(kind, f"status {exc.status}") from exc
        except sdk.TypeSafeError as exc:
            raise ProviderError("client_error", type(exc).__name__) from exc
        try:
            answers = [_answer(item, response.answers[f"q{index}"]) for index, item in enumerate(items)]
        except (KeyError, AttributeError, TypeError, ValueError) as exc:
            raise ProviderError("invalid_output", "an answer is missing or of the wrong type") from exc
        usage = response.usage
        return ProviderResult(
            output={"answers": answers},
            input_tokens=max(0, usage.input_tokens or 0), output_tokens=max(0, usage.output_tokens or 0),
            request_id=response.__dict__.get("_request_id"), resolved_model=response.model,
        )
