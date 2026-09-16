"""Explicit provider-neutral inference configuration and bounded HTTP protocols.

The complete object is picklable for isolated native workers. It contains only
one inference credential; database, broker and other provider secrets stay out.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from money.data.security import ProviderFailure, _PinnedHTTPS, public_addresses, validate_url
from money.research.call_telemetry import InferenceReceipt, emit_calls
from money.schemas.contracts import Usage


def strict_response_json(raw: bytes) -> dict:
    def unique(pairs: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("INFERENCE_DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError("INFERENCE_NONFINITE_JSON")

    result = json.loads(raw, object_pairs_hook=unique, parse_constant=invalid_constant)
    if not isinstance(result, dict):
        raise ValueError("INFERENCE_RESPONSE_INVALID")
    return result


@dataclass(frozen=True)
class InferenceConfiguration:
    provider: str
    model: str
    endpoint: str
    api_key: str = field(repr=False)
    protocol: Literal["openai-compatible", "anthropic"] = "openai-compatible"
    temperature: float | None = 0.0
    reasoning_effort: str | None = None
    max_output_tokens: int = 4096
    maximum_prompt_bytes: int = 100000
    timeout_seconds: float = 60
    structured_output: bool = True
    input_gbp_per_million: Decimal | None = None
    output_gbp_per_million: Decimal | None = None

    def __post_init__(self) -> None:
        host = urlsplit(self.endpoint).hostname
        if not host or not self.api_key or not self.provider or not self.model:
            raise ValueError("INFERENCE_CONFIGURATION_INCOMPLETE")
        if (
            len(self.model) > 200
            or len(self.provider) > 128
            or self.reasoning_effort
            not in (None, "none", "minimal", "low", "medium", "high", "xhigh")
        ):
            raise ValueError("INFERENCE_CONFIGURATION_INVALID")
        validate_url(self.endpoint, frozenset({host}))
        if (
            self.protocol not in ("openai-compatible", "anthropic")
            or not 1 <= self.max_output_tokens <= 16000
            or not 0 < self.timeout_seconds <= 180
        ):
            raise ValueError("INFERENCE_CONFIGURATION_INVALID")
        if not 100 <= self.maximum_prompt_bytes <= 1000000:
            raise ValueError("INFERENCE_PROMPT_LIMIT_INVALID")
        if any(
            rate is not None and (not rate.is_finite() or rate < 0)
            for rate in (self.input_gbp_per_million, self.output_gbp_per_million)
        ):
            raise ValueError("INFERENCE_COST_INVALID")


class HTTPInference:
    def __init__(self, configuration: InferenceConfiguration) -> None:
        self.configuration = configuration
        self.provider, self.model = configuration.provider, configuration.model
        self._usages: list[Usage] = []

    @property
    def allowed_network_hosts(self) -> tuple[str, ...]:
        return (urlsplit(self.configuration.endpoint).hostname or "",)

    def complete(self, system: str, user: str) -> str:
        started = time.monotonic()
        before = len(self._usages)
        sent = False
        status = "FAILED"
        error_code: str | None = "INFERENCE_FAILED"
        try:
            # Reject oversized inputs before counting a provider operation.
            if len((system + user).encode()) > self.configuration.maximum_prompt_bytes:
                raise ValueError("TOKEN_INPUT_LIMIT")
            sent = True
            result = self._complete(system, user)
            status, error_code = "SUCCEEDED", None
            return result
        except ProviderFailure as error:
            if error.code in {"PROVIDER_TIMEOUT", "PROVIDER_UNAVAILABLE"}:
                error_code = error.code
            raise
        finally:
            usage = self._usages[-1] if len(self._usages) > before else Usage()
            emit_calls((InferenceReceipt.model_validate({
                "provider": self.provider, "model": self.model,
                "duration_seconds": time.monotonic() - started,
                "status": status, "error_code": error_code,
                "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                # Configured rate calculations are estimates, never invoice charges.
                "estimated_cost": usage.cost_gbp, "actual_cost": None,
                "currency": "GBP" if usage.cost_gbp is not None else None,
                "provider_calls": int(sent), "cache_hit": False, "retry": False,
            }),))

    def _complete(self, system: str, user: str) -> str:
        config = self.configuration
        if len((system + user).encode()) > config.maximum_prompt_bytes:
            raise ValueError("TOKEN_INPUT_LIMIT")
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        if config.protocol == "anthropic":
            body = {
                "model": self.model,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "max_tokens": config.max_output_tokens,
            }
            headers.update({"x-api-key": config.api_key, "anthropic-version": "2023-06-01"})
        else:
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "max_completion_tokens": config.max_output_tokens,
            }
            headers["Authorization"] = "Bearer " + config.api_key
            if config.reasoning_effort is not None:
                body["reasoning_effort"] = config.reasoning_effort
        if config.temperature is not None:
            body["temperature"] = config.temperature
        # Native specialists produce prose, then a strict final Money JSON contract.
        # structured_output declares capability; it is not forced on prose tasks.
        result = self._post(body, headers)
        self._record_usage(result)
        if result.get("model") != self.model:
            # Pin an exact provider model ID; do not silently accept an alias change.
            raise ValueError("INFERENCE_MODEL_MISMATCH")
        if config.protocol == "anthropic":
            if result.get("stop_reason") != "end_turn":
                raise ValueError("INFERENCE_INCOMPLETE")
            blocks = result.get("content", [])
            if any(block.get("type") != "text" for block in blocks):
                raise ValueError("INFERENCE_TOOL_OUTPUT_DENIED")
            value = "".join(block["text"] for block in blocks)
        else:
            choices = result.get("choices", [])
            if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                raise ValueError("INFERENCE_INCOMPLETE")
            message = choices[0]["message"]
            if message.get("tool_calls") or message.get("function_call"):
                raise ValueError("INFERENCE_TOOL_OUTPUT_DENIED")
            value = message.get("content")
        if not isinstance(value, str) or not value or len(value) > 100000:
            raise ValueError("INFERENCE_OUTPUT_INVALID")
        return value

    def _record_usage(self, result: dict) -> None:
        config = self.configuration
        usage = result.get("usage", {})
        if usage is None:
            usage = {}
        if not isinstance(usage, dict):
            raise ValueError("INFERENCE_USAGE_INVALID")
        input_tokens, output_tokens = (
            (usage.get("input_tokens"), usage.get("output_tokens"))
            if config.protocol == "anthropic"
            else (usage.get("prompt_tokens"), usage.get("completion_tokens"))
        )
        if any(
            value is not None and type(value) is not int for value in (input_tokens, output_tokens)
        ):
            raise ValueError("INFERENCE_USAGE_INVALID")
        measured = Usage(input_tokens=input_tokens, output_tokens=output_tokens)
        input_tokens, output_tokens = measured.input_tokens, measured.output_tokens
        cost = None
        if (
            result.get("model") == config.model
            and input_tokens is not None
            and output_tokens is not None
            and config.input_gbp_per_million is not None
            and config.output_gbp_per_million is not None
        ):
            cost = (
                input_tokens * config.input_gbp_per_million
                + output_tokens * config.output_gbp_per_million
            ) / 1000000
        self._usages.append(
            Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_gbp=cost)
        )

    def _post(self, body: dict, headers: dict[str, str]) -> dict:
        config = self.configuration
        host, path = validate_url(config.endpoint, frozenset(self.allowed_network_hosts))
        deadline = time.monotonic() + config.timeout_seconds
        address = public_addresses(host, min(config.timeout_seconds, 5))[0]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True)
        connection = _PinnedHTTPS(host, address, min(remaining, 5))
        absolute_deadline: threading.Timer | None = None
        try:
            connection.request(
                "POST", path, body=json.dumps(body, allow_nan=False).encode(), headers=headers
            )
            assert connection.sock is not None
            stream_socket = connection.sock
            stream_socket.settimeout(max(0.001, deadline - time.monotonic()))

            def interrupt() -> None:
                try:
                    stream_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

            absolute_deadline = threading.Timer(max(0.001, deadline - time.monotonic()), interrupt)
            absolute_deadline.daemon = True
            absolute_deadline.start()
            response = connection.getresponse()
            if response.status != 200:
                raise ProviderFailure(
                    "PROVIDER_UNAVAILABLE",
                    retryable=response.status == 429 or response.status >= 500,
                )
            if (
                response.getheader("Content-Encoding", "identity") != "identity"
                or response.getheader("Content-Type", "").split(";")[0] != "application/json"
            ):
                raise ValueError("INFERENCE_RESPONSE_INVALID")
            chunks, size = [], 0
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError
                stream_socket.settimeout(max(0.001, deadline - time.monotonic()))
                chunk = response.read1(min(65536, 1_000_001 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > 1_000_000:
                    raise ValueError("INFERENCE_RESPONSE_TOO_LARGE")
            return strict_response_json(b"".join(chunks))
        except TimeoutError as error:
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True) from error
        except (OSError, http.client.HTTPException) as error:
            raise ProviderFailure("PROVIDER_UNAVAILABLE", retryable=True) from error
        finally:
            if absolute_deadline:
                absolute_deadline.cancel()
            connection.close()

    def usage(self) -> Usage:
        if not self._usages:
            return Usage()

        def total(name: str) -> object:
            values = [getattr(item, name) for item in self._usages]
            return sum(values) if all(value is not None for value in values) else None

        return Usage.model_validate(
            {name: total(name) for name in ("input_tokens", "output_tokens", "cost_gbp")}
        )
