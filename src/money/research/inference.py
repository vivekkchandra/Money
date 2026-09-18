"""Explicit provider-neutral inference configuration and bounded HTTP protocols.

The complete object is picklable for isolated native workers. It contains at most
one inference credential; database, broker and other provider secrets stay out.
"""

from __future__ import annotations

import http.client
import ipaddress
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

Authentication = Literal["none", "bearer", "api-key"]
EndpointScope = Literal["public", "local"]


def validate_reasoning_effort(provider: str, protocol: str, effort: str | None) -> None:
    """Admit only explicitly supported controls; never add a provider default."""
    if effort is None:
        return
    supported = {
        "openai": ("none", "minimal", "low", "medium", "high", "xhigh"),
        # Verified against Ollama's OpenAI-compatible Qwen3 endpoint. Other
        # reasoning levels have different semantics and are not assumed supported.
        "ollama": ("none",),
    }
    if protocol != "openai-compatible" or effort not in supported.get(provider, ()):
        raise ValueError("INFERENCE_REASONING_CONTROL_UNSUPPORTED")


def inference_endpoint(endpoint: str, scope: EndpointScope) -> tuple[str, str, int]:
    """Validate an inference destination without consulting credentials or DNS.

    Public connections retain the provider transport's public-address DNS pinning.
    Explicit local connections are restricted to loopback and bypass DNS entirely.
    Query strings/userinfo are prohibited so configuration cannot carry credentials.
    """
    try:
        parts = urlsplit(endpoint)
        host = (parts.hostname or "").lower()
        if (
            not host
            or len(endpoint) > 4096
            or parts.username is not None
            or parts.password is not None
            or parts.query
            or parts.fragment
            or host.endswith(".")
            or "\\" in endpoint
            or any(ord(char) < 33 or ord(char) > 126 for char in endpoint)
        ):
            raise ValueError
        address = None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            pass
        if scope == "local":
            port = 80 if parts.port is None else parts.port
            if (
                parts.scheme != "http"
                or not (host == "localhost" or (address is not None and address.is_loopback))
                or not 1 <= port <= 65535
            ):
                raise ValueError
            return host, parts.path or "/", port
        if scope != "public" or (
            host == "localhost"
            or host.endswith((".localhost", ".local", ".internal"))
            or (address is None and "." not in host)
            or (address is not None and (not address.is_global or address.is_multicast))
        ):
            raise ValueError
        host, path = validate_url(endpoint, frozenset({host}))
        return host, path, 443
    except ValueError as error:
        raise ValueError("INFERENCE_ENDPOINT_DENIED") from error


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
    api_key: str | None = field(default=None, repr=False)
    protocol: Literal["openai-compatible", "anthropic"] = "openai-compatible"
    temperature: float | None = 0.0
    reasoning_effort: str | None = None
    max_output_tokens: int = 4096
    maximum_prompt_bytes: int = 100000
    timeout_seconds: float = 60
    structured_output: bool = True
    input_gbp_per_million: Decimal | None = None
    output_gbp_per_million: Decimal | None = None
    authentication: Authentication | None = None
    endpoint_scope: EndpointScope = "public"

    def __post_init__(self) -> None:
        if self.authentication is None:
            # Preserve old checked-in selections without introducing a fake key.
            object.__setattr__(
                self, "authentication", "api-key" if self.protocol == "anthropic" else "bearer"
            )
        if not self.provider or not self.model:
            raise ValueError("INFERENCE_CONFIGURATION_INCOMPLETE")
        if self.authentication not in ("none", "bearer", "api-key"):
            raise ValueError("INFERENCE_AUTHENTICATION_INVALID")
        if self.authentication == "none":
            if self.api_key is not None:
                raise ValueError("INFERENCE_UNUSED_CREDENTIAL_DENIED")
            if self.endpoint_scope != "local":
                raise ValueError("INFERENCE_UNAUTHENTICATED_PUBLIC_ENDPOINT_DENIED")
        elif not self.api_key:
            raise ValueError("INFERENCE_CREDENTIAL_MISSING")
        if self.api_key is not None and (
            len(self.api_key) > 8192
            or any(ord(char) < 33 or ord(char) > 126 for char in self.api_key)
        ):
            raise ValueError("INFERENCE_CREDENTIAL_INVALID")
        if (
            len(self.model) > 200
            or len(self.provider) > 128
            or self.reasoning_effort
            not in (None, "none", "minimal", "low", "medium", "high", "xhigh")
        ):
            raise ValueError("INFERENCE_CONFIGURATION_INVALID")
        inference_endpoint(self.endpoint, self.endpoint_scope)
        validate_reasoning_effort(self.provider, self.protocol, self.reasoning_effort)
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
        self.last_response_model: str | None = None
        self.last_finish_reason: str | None = None

    @property
    def allowed_network_hosts(self) -> tuple[str, ...]:
        return (urlsplit(self.configuration.endpoint).hostname or "",)

    @property
    def allowed_network_port(self) -> int:
        return inference_endpoint(self.configuration.endpoint, self.configuration.endpoint_scope)[2]

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept-Encoding": "identity"}
        config = self.configuration
        if config.authentication == "bearer":
            assert config.api_key is not None
            headers["Authorization"] = "Bearer " + config.api_key
        elif config.authentication == "api-key":
            assert config.api_key is not None
            headers["x-api-key"] = config.api_key
        if config.protocol == "anthropic":
            headers["anthropic-version"] = "2023-06-01"
        return headers

    def available_models(self) -> tuple[str, ...]:
        """Read a bounded OpenAI-compatible model catalogue on this same origin."""
        config = self.configuration
        _, path, _ = inference_endpoint(config.endpoint, config.endpoint_scope)
        if config.protocol != "openai-compatible" or not path.endswith("/chat/completions"):
            raise ValueError("INFERENCE_MODEL_CATALOGUE_UNSUPPORTED")
        result = self._request(
            "GET", path.removesuffix("/chat/completions") + "/models", None, self._headers()
        )
        records = result.get("data")
        if not isinstance(records, list) or not records or len(records) > 10000:
            raise ValueError("INFERENCE_MODEL_CATALOGUE_INVALID")
        models: list[str] = []
        for record in records:
            value = record.get("id") if isinstance(record, dict) else None
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError("INFERENCE_MODEL_CATALOGUE_INVALID")
            models.append(value)
        if len(models) != len(set(models)):
            raise ValueError("INFERENCE_MODEL_CATALOGUE_INVALID")
        return tuple(models)

    def complete(self, system: str, user: str) -> str:
        started = time.monotonic()
        self.last_response_model = None
        self.last_finish_reason = None
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
        except ValueError as error:
            # Only fixed Money codes, never provider text, prompts or credentials.
            if str(error) in {
                "TOKEN_INPUT_LIMIT", "INFERENCE_EMPTY_RESPONSE", "INFERENCE_OUTPUT_INVALID",
                "INFERENCE_INCOMPLETE", "INFERENCE_RESPONSE_INVALID", "INFERENCE_USAGE_INVALID",
                "INFERENCE_MODEL_MISMATCH", "INFERENCE_TOOL_OUTPUT_DENIED",
                "INFERENCE_RESPONSE_TOO_LARGE", "INFERENCE_DUPLICATE_JSON_KEY",
                "INFERENCE_NONFINITE_JSON",
            }:
                error_code = str(error)
            raise
        finally:
            usage = self._usages[-1] if len(self._usages) > before else Usage()
            emit_calls(
                (
                    InferenceReceipt.model_validate(
                        {
                            "provider": self.provider,
                            "model": self.model,
                            "duration_seconds": time.monotonic() - started,
                            "status": status,
                            "error_code": error_code,
                            "input_tokens": usage.input_tokens,
                            "output_tokens": usage.output_tokens,
                            # Configured rate calculations are estimates, never invoice charges.
                            "estimated_cost": usage.cost_gbp,
                            "actual_cost": None,
                            "currency": "GBP" if usage.cost_gbp is not None else None,
                            "provider_calls": int(sent),
                            "cache_hit": False,
                            "retry": False,
                        }
                    ),
                )
            )

    def _complete(self, system: str, user: str) -> str:
        config = self.configuration
        if len((system + user).encode()) > config.maximum_prompt_bytes:
            raise ValueError("TOKEN_INPUT_LIMIT")
        headers = self._headers()
        self.last_response_model = None
        if config.protocol == "anthropic":
            body = {
                "model": self.model,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "max_tokens": config.max_output_tokens,
            }
        else:
            body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
            token_field = "max_completion_tokens" if config.provider == "openai" else "max_tokens"
            body[token_field] = config.max_output_tokens
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
        self.last_response_model = self.model
        value: object
        if config.protocol == "anthropic":
            if result.get("stop_reason") != "end_turn":
                raise ValueError("INFERENCE_INCOMPLETE")
            self.last_finish_reason = "end_turn"
            blocks = result.get("content", [])
            if not isinstance(blocks, list) or any(not isinstance(block, dict) for block in blocks):
                raise ValueError("INFERENCE_RESPONSE_INVALID")
            if any(block.get("type") != "text" for block in blocks):
                raise ValueError("INFERENCE_TOOL_OUTPUT_DENIED")
            if any(not isinstance(block.get("text"), str) for block in blocks):
                raise ValueError("INFERENCE_RESPONSE_INVALID")
            value = "".join(block["text"] for block in blocks)
        else:
            choices = result.get("choices", [])
            if (
                not isinstance(choices, list)
                or len(choices) != 1
                or not isinstance(choices[0], dict)
                or choices[0].get("finish_reason") != "stop"
            ):
                raise ValueError("INFERENCE_INCOMPLETE")
            self.last_finish_reason = "stop"
            message = choices[0].get("message")
            if not isinstance(message, dict):
                raise ValueError("INFERENCE_RESPONSE_INVALID")
            if message.get("role", "assistant") != "assistant":
                raise ValueError("INFERENCE_RESPONSE_INVALID")
            if message.get("tool_calls") or message.get("function_call"):
                raise ValueError("INFERENCE_TOOL_OUTPUT_DENIED")
            value = message.get("content")
        if value is None or (isinstance(value, str) and not value.strip()):
            raise ValueError("INFERENCE_EMPTY_RESPONSE")
        if not isinstance(value, str) or len(value) > 100000:
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
        _, path, _ = inference_endpoint(config.endpoint, config.endpoint_scope)
        return self._request("POST", path, body, headers)

    def _request(self, method: str, path: str, body: dict | None, headers: dict[str, str]) -> dict:
        config = self.configuration
        host, _, port = inference_endpoint(config.endpoint, config.endpoint_scope)
        deadline = time.monotonic() + config.timeout_seconds
        address = (
            ("127.0.0.1" if host == "localhost" else host)
            if config.endpoint_scope == "local"
            else public_addresses(host, min(config.timeout_seconds, 5))[0]
        )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True)
        connection = (
            http.client.HTTPConnection(address, port, timeout=min(remaining, 5))
            if config.endpoint_scope == "local"
            else _PinnedHTTPS(host, address, min(remaining, 5))
        )
        absolute_deadline: threading.Timer | None = None
        try:
            connection.request(
                method,
                path,
                body=json.dumps(body, allow_nan=False).encode() if body is not None else None,
                headers=headers,
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
            if time.monotonic() >= deadline:
                raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True) from error
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
