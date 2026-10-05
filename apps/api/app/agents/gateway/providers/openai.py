"""OpenAI adapter: Responses API with structured output (ADR 0009 §9).

The only module that imports the ``openai`` SDK for the gateway. The key is read
only from ``DCLAB_OPENAI_API_KEY``. One request per call, no SDK retries
(``max_retries=0``), ``store=False``. Returns usage, the request id and the
provider-reported resolved model (OpenAI publishes no dated snapshots for the
pinned ids, so the ledger records what actually answered).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from app.agents.gateway.providers import ProviderCall, ProviderError, ProviderResult

API_KEY_ENV = "DCLAB_OPENAI_API_KEY"


def _int(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


class OpenAIProvider:
    name = "openai"

    def __init__(self, *, client_factory: Callable[..., Any] | None = None) -> None:
        self._client_factory = client_factory

    def _client(self, timeout_s: float) -> Any:
        api_key = os.environ.get(API_KEY_ENV, "").strip()
        if not api_key:
            raise ProviderError("not_configured", f"{API_KEY_ENV} is not set")
        if self._client_factory is not None:
            return self._client_factory(api_key=api_key, timeout=timeout_s, max_retries=0)
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ProviderError("not_configured", "openai SDK unavailable") from exc
        return OpenAI(api_key=api_key, timeout=timeout_s, max_retries=0)

    def complete(self, call: ProviderCall) -> ProviderResult:
        client = self._client(call.timeout_s)
        import openai

        options: dict[str, Any] = {}
        if call.temperature is not None:
            options["temperature"] = call.temperature
        try:
            response = client.responses.parse(
                model=call.model,
                instructions=call.instructions,
                input=call.input_json,
                text_format=call.output_schema,
                max_output_tokens=call.max_output_tokens,
                store=False,
                **options,
            )
        except openai.APITimeoutError as exc:
            raise ProviderError("timeout") from exc
        except openai.APIConnectionError as exc:
            raise ProviderError("server_error", "connection failed") from exc
        except openai.RateLimitError as exc:
            raise ProviderError("rate_limited") from exc
        except openai.APIStatusError as exc:
            kind = "server_error" if exc.status_code >= 500 else "client_error"
            raise ProviderError(kind, f"status {exc.status_code}") from exc
        except (openai.LengthFinishReasonError, openai.ContentFilterFinishReasonError) as exc:
            raise ProviderError("invalid_output", type(exc).__name__) from exc
        except openai.OpenAIError as exc:
            raise ProviderError("invalid_output", type(exc).__name__) from exc
        except ValueError as exc:  # pydantic parse failure of the structured output
            raise ProviderError("invalid_output", type(exc).__name__) from exc
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise ProviderError("invalid_output", "no structured output")
        usage = getattr(response, "usage", None)
        return ProviderResult(
            output=parsed.model_dump(mode="json"),
            input_tokens=_int(getattr(usage, "input_tokens", None)),
            output_tokens=_int(getattr(usage, "output_tokens", None)),
            request_id=getattr(response, "_request_id", None) or getattr(response, "id", None),
            resolved_model=getattr(response, "model", None),
        )
