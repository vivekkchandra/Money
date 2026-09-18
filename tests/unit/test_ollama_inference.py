"""Shared provider transport contract; no Ollama or paid service needed in unit tests."""

import json
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import pytest

from money.data.security import ProviderFailure
from money.research import inference as transport
from money.research.call_telemetry import capture_calls
from money.research.inference import HTTPInference, InferenceConfiguration
from money.research.inference_config import InferenceSelection, load_inference_selections

REPO = Path(__file__).resolve().parents[2]


def ollama_selection(**overrides):
    return InferenceSelection.model_validate(
        {
            "provider": "ollama",
            "model": "qwen3:14b",
            "endpoint": "http://127.0.0.1:11434/v1/chat/completions",
            "protocol": "openai-compatible",
            "authentication": "none",
            "endpoint_scope": "local",
        }
        | overrides
    )


def answer(**overrides):
    return {
        "model": "qwen3:14b",
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "OK",
                    "reasoning": "Private text is not output.",
                },
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2},
    } | overrides


class StubInference(HTTPInference):
    def __init__(self, configuration, response):
        super().__init__(configuration)
        self.response = response
        self.requests = []

    def _post(self, body, headers):
        self.requests.append((body, headers))
        return self.response


class StubSocket:
    def settimeout(self, value):
        assert value > 0

    def shutdown(self, _):
        pass


class StubResponse:
    def __init__(self, content, status=200):
        self.content = BytesIO(content)
        self.status = status

    def getheader(self, name, default=None):
        return {"Content-Type": "application/json"}.get(name, default)

    def read1(self, size):
        return self.content.read(size)


class StubConnection:
    def __init__(self, response):
        self.response = response
        self.sock = StubSocket()
        self.requests = []
        self.closed = False

    def request(self, *args, **kwargs):
        self.requests.append((args, kwargs))

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def test_checked_in_ollama_configuration_has_all_three_roles_no_credentials():
    selected = load_inference_selections(
        REPO,
        {
            "MONEY_INFERENCE_CONFIG": "data/configuration/ollama-inference.json",
            "OPENAI_API_KEY": "not-read-in-ollama-mode",
        },
    )
    assert set(selected) == {"tradingagents", "ai_hedge_fund", "crewai"}
    for selection in selected.values():
        assert selection.model == "qwen3:14b"
        assert selection.authentication == "none"
        assert selection.reasoning_effort == "none"
        assert selection.timeout_seconds == 180
        assert selection.credential_environment_variable is None
        client = selection.inference({})
        assert client.configuration.api_key is None
        assert client.configuration.reasoning_effort == "none"
        assert client.allowed_network_port == 11434
        assert client.allowed_network_hosts == ("127.0.0.1",)


def test_config_selection_is_explicit_repo_relative_and_preserves_original_openai():
    baseline = load_inference_selections(REPO, {})
    assert all(selection.provider == "openai" for selection in baseline.values())
    assert all(selection.authentication == "bearer" for selection in baseline.values())
    assert all(
        selection.credential_environment_variable == "OPENAI_API_KEY"
        for selection in baseline.values()
    )
    absolute = str(REPO / "data/configuration/ollama-inference.json")
    assert all(
        selection.is_local
        for selection in load_inference_selections(
            REPO, {"MONEY_INFERENCE_CONFIG": absolute}
        ).values()
    )
    for value in ("", "missing.json", "../outside.json"):
        with pytest.raises(ValueError, match="INFERENCE_CONFIG_INVALID"):
            load_inference_selections(REPO, {"MONEY_INFERENCE_CONFIG": value})


def test_config_does_not_follow_symlink_or_accept_extra_roles_or_duplicate_json(tmp_path):
    linked = tmp_path / "linked.json"
    linked.symlink_to(REPO / "data/configuration/ollama-inference.json")
    with pytest.raises(ValueError, match="INFERENCE_CONFIG_INVALID"):
        load_inference_selections(tmp_path, {"MONEY_INFERENCE_CONFIG": "linked.json"})
    for raw in (b'{"tradingagents":{},"tradingagents":{}}', b'{"extra":{}}', b"[]"):
        (tmp_path / "invalid.json").write_bytes(raw)
        with pytest.raises(ValueError, match="INFERENCE_CONFIG_INVALID"):
            load_inference_selections(tmp_path, {"MONEY_INFERENCE_CONFIG": "invalid.json"})


def test_auth_none_reads_no_environment_variable():
    class ForbiddenEnvironment(dict):
        def get(self, *args):
            raise AssertionError("No credential may be read for authentication:none")

    provider = ollama_selection().inference(ForbiddenEnvironment())
    client = StubInference(provider.configuration, answer())
    assert client.complete("policy", "Reply with exactly OK") == "OK"
    body, headers = client.requests[0]
    assert "Authorization" not in headers and "x-api-key" not in headers
    assert body["max_tokens"] == provider.configuration.max_output_tokens
    assert "max_completion_tokens" not in body and "reasoning_effort" not in body
    assert client.last_response_model == "qwen3:14b"


def test_authenticated_openai_and_generic_hosted_provider_remain_supported():
    for provider in ("openai", "reviewed-open-source-host"):
        selected = InferenceSelection(
            provider=provider,
            model="exact-version",
            endpoint="https://inference.example.test/v1/chat/completions",
            authentication="bearer",
            credential_environment_variable="INFERENCE_TOKEN",
        )
        with pytest.raises(ValueError, match="INFERENCE_CREDENTIAL_MISSING"):
            selected.inference({})
        selected.require_hosted()
        client = StubInference(
            selected.inference({"INFERENCE_TOKEN": "test-secret"}).configuration,
            answer(model="exact-version"),
        )
        assert client.complete("policy", "facts") == "OK"
        body, headers = client.requests[0]
        assert headers["Authorization"] == "Bearer test-secret"
        assert "x-api-key" not in headers
        assert ("max_completion_tokens" if provider == "openai" else "max_tokens") in body
        assert "reasoning_effort" not in body
        assert "test-secret" not in repr(client.configuration)


def test_api_key_authentication_is_distinct_from_bearer():
    selected = InferenceSelection(
        provider="hosted",
        model="exact-version",
        endpoint="https://inference.example.test/v1/chat/completions",
        authentication="api-key",
        credential_environment_variable="INFERENCE_TOKEN",
    )
    client = StubInference(
        selected.inference({"INFERENCE_TOKEN": "test-secret"}).configuration,
        answer(model="exact-version"),
    )
    client.complete("policy", "facts")
    assert client.requests[0][1]["x-api-key"] == "test-secret"
    assert "Authorization" not in client.requests[0][1]


def test_reasoning_never_contaminates_final_content():
    client = StubInference(ollama_selection().inference({}).configuration, answer())
    assert client.complete("policy", "facts") == "OK"
    client.response = answer(choices=[{"finish_reason": "stop", "message": {"reasoning": "OK"}}])
    with pytest.raises(ValueError, match="INFERENCE_EMPTY_RESPONSE"):
        client.complete("policy", "facts")
    with pytest.raises(ValueError, match="INFERENCE_REASONING_CONTROL_UNSUPPORTED"):
        ollama_selection(reasoning_effort="low")


@pytest.mark.parametrize(
    "changes",
    [
        {"credential_environment_variable": "OPENAI_API_KEY"},
        {"endpoint_scope": "public"},
        {"endpoint": "http://192.168.1.10:11434/v1/chat/completions"},
        {"endpoint": "http://0.0.0.0:11434/v1/chat/completions"},
        {"endpoint": "http://example.com:11434/v1/chat/completions"},
        {"endpoint": "http://127.0.0.1:11434/v1/chat/completions?api_key=unacceptable"},
        {"endpoint": "http://user:password@127.0.0.1:11434/v1/chat/completions"},
    ],
)
def test_none_authentication_and_local_endpoint_are_explicit(changes):
    with pytest.raises(ValueError):
        ollama_selection(**changes)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:11434/v1/chat/completions",
        "http://127.0.0.1:11434/v1/chat/completions",
        "http://[::1]:11434/v1/chat/completions",
    ],
)
def test_all_supported_loopbacks_are_denied_for_hosted_production(endpoint):
    selection = ollama_selection(endpoint=endpoint)
    with pytest.raises(ValueError, match="HOSTED_LOCAL_INFERENCE_DENIED"):
        selection.require_hosted()
    with pytest.raises(ValueError):
        ollama_selection(
            endpoint=endpoint,
            endpoint_scope="public",
            authentication="bearer",
            credential_environment_variable="INFERENCE_TOKEN",
        )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://localhost/v1/chat/completions",
        "https://127.0.0.1/v1/chat/completions",
        "https://[::1]/v1/chat/completions",
        "https://10.0.0.2/v1/chat/completions",
        "https://service.railway.internal/v1/chat/completions",
    ],
)
def test_public_selection_cannot_disguise_private_destination(endpoint):
    with pytest.raises(ValueError, match="INFERENCE_ENDPOINT_DENIED"):
        InferenceSelection(
            provider="hosted",
            model="pinned",
            endpoint=endpoint,
            credential_environment_variable="INFERENCE_TOKEN",
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"model": "unexpected-version"},
        {"choices": None},
        {"choices": {}},
        {"choices": [None]},
        {"choices": [{"finish_reason": "stop", "message": None}]},
        {"choices": [{"finish_reason": "stop", "message": {"content": "  "}}]},
        {"choices": [{"finish_reason": "stop", "message": {"role": "user", "content": "OK"}}]},
        {"choices": [{"finish_reason": "length", "message": {"content": "OK"}}]},
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "OK",
                        "tool_calls": [{"name": "execute_trade"}],
                    },
                }
            ]
        },
    ],
)
def test_malformed_switched_truncated_or_tool_output_fails_closed(payload):
    client = StubInference(ollama_selection().inference({}).configuration, answer(**payload))
    with pytest.raises(ValueError):
        client.complete("policy", "facts")


def test_local_http_pins_port_ignores_proxies_and_has_no_authorization(monkeypatch):
    connection = StubConnection(StubResponse(json.dumps(answer()).encode()))
    connections = []

    def create(host, port, timeout):
        connections.append((host, port, timeout))
        return connection

    def forbidden(*args, **kwargs):
        raise AssertionError("Local loopback must not use public DNS, HTTPS or a proxy")

    monkeypatch.setattr(transport.http.client, "HTTPConnection", create)
    monkeypatch.setattr(transport, "public_addresses", forbidden)
    monkeypatch.setattr(transport, "_PinnedHTTPS", forbidden)
    monkeypatch.setenv("HTTP_PROXY", "http://proxy.invalid:3128")
    client = ollama_selection(endpoint="http://localhost:11434/v1/chat/completions").inference({})
    assert client.complete("policy", "facts") == "OK"
    assert connections[0][0:2] == ("127.0.0.1", 11434)
    assert "Authorization" not in connection.requests[0][1]["headers"]
    assert connection.closed


def test_non_thinking_control_reaches_serialized_http_body(monkeypatch):
    connection = StubConnection(StubResponse(json.dumps(answer()).encode()))
    monkeypatch.setattr(transport.http.client, "HTTPConnection", lambda *a, **k: connection)
    selected = ollama_selection(reasoning_effort="none", timeout_seconds=180)
    # Same JSON round trip as the isolated child boundary.
    client = InferenceSelection.model_validate_json(selected.model_dump_json()).inference({})
    assert client.complete("policy", "Reply with exactly OK") == "OK"
    args, kwargs = connection.requests[0]
    assert args == ("POST", "/v1/chat/completions")
    payload = json.loads(kwargs["body"])
    assert payload == {
        "model": "qwen3:14b",
        "messages": [
            {"role": "system", "content": "policy"},
            {"role": "user", "content": "Reply with exactly OK"},
        ],
        "temperature": 0,
        "max_tokens": selected.max_output_tokens,
        "reasoning_effort": "none",
    }
    assert "Authorization" not in kwargs["headers"]
    assert client.last_response_model == "qwen3:14b"
    assert client.last_finish_reason == "stop"
    assert client.configuration.timeout_seconds == 180


@pytest.mark.parametrize("provider,protocol,effort", [
    ("ollama", "openai-compatible", "low"),
    ("unverified-host", "openai-compatible", "none"),
    ("ollama", "anthropic", "none"),
    ("openai", "anthropic", "none"),
])
def test_unsupported_reasoning_controls_rejected_by_both_models(provider, protocol, effort):
    with pytest.raises(ValueError, match="INFERENCE_REASONING_CONTROL_UNSUPPORTED"):
        ollama_selection(provider=provider, protocol=protocol, reasoning_effort=effort)
    with pytest.raises(ValueError, match="INFERENCE_REASONING_CONTROL_UNSUPPORTED"):
        replace(ollama_selection().inference({}).configuration,
                provider=provider, protocol=protocol, reasoning_effort=effort)


def test_openai_reasoning_control_still_reaches_request():
    selection = load_inference_selections(REPO, {})["tradingagents"]
    client = StubInference(selection.inference({"OPENAI_API_KEY": "test-secret"}).configuration,
                           answer(model=selection.model))
    assert client.complete("policy", "facts") == "OK"
    body, _ = client.requests[0]
    assert body["reasoning_effort"] == "low"
    assert "max_completion_tokens" in body and "max_tokens" not in body


def test_empty_visible_response_has_precise_secret_free_receipt():
    client = StubInference(ollama_selection(reasoning_effort="none").inference({}).configuration,
        answer(choices=[{"finish_reason": "stop", "message": {
            "content": "", "reasoning": "private-test-text",
        }}]))
    with capture_calls() as calls, pytest.raises(ValueError, match="INFERENCE_EMPTY_RESPONSE"):
        client.complete("policy", "facts")
    assert calls[0].error_code == "INFERENCE_EMPTY_RESPONSE"
    assert calls[0].provider_calls == 1
    assert calls[0].status == "FAILED"
    assert "private-test-text" not in calls[0].model_dump_json()


@pytest.mark.parametrize(
    "error,code",
    [
        (ConnectionRefusedError(), "PROVIDER_UNAVAILABLE"),
        (TimeoutError(), "PROVIDER_TIMEOUT"),
    ],
)
def test_server_unavailable_and_timeout_fail_closed(monkeypatch, error, code):
    connection = StubConnection(None)

    def fail(*args, **kwargs):
        raise error

    connection.request = fail
    monkeypatch.setattr(transport.http.client, "HTTPConnection", lambda *a, **k: connection)
    with pytest.raises(ProviderFailure, match=code):
        ollama_selection(reasoning_effort="none").inference({}).complete("policy", "facts")
    assert connection.closed


def test_redirect_is_not_followed(monkeypatch):
    connection = StubConnection(StubResponse(b"", status=302))
    monkeypatch.setattr(transport.http.client, "HTTPConnection", lambda *a, **k: connection)
    with pytest.raises(ProviderFailure, match="PROVIDER_UNAVAILABLE"):
        ollama_selection().inference({}).complete("policy", "facts")
    assert len(connection.requests) == 1


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"model":"a","model":"b"}'])
def test_malformed_wire_response_fails_closed(monkeypatch, raw):
    connection = StubConnection(StubResponse(raw))
    monkeypatch.setattr(transport.http.client, "HTTPConnection", lambda *a, **k: connection)
    with pytest.raises(ValueError):
        ollama_selection().inference({}).complete("policy", "facts")
    assert connection.closed


def test_failure_does_not_reuse_previous_returned_model_identity():
    client = StubInference(ollama_selection().inference({}).configuration, answer())
    client.complete("policy", "facts")
    assert client.last_response_model == "qwen3:14b"
    with pytest.raises(ValueError, match="TOKEN_INPUT_LIMIT"):
        client.complete("policy", "x" * 30001)
    assert client.last_response_model is None
    assert client.last_finish_reason is None


def test_model_catalogue_uses_same_origin_and_strict_ids(monkeypatch):
    connection = StubConnection(StubResponse(b'{"data":[{"id":"qwen3:14b"}]}'))
    monkeypatch.setattr(transport.http.client, "HTTPConnection", lambda *a, **k: connection)
    client = ollama_selection().inference({})
    assert client.available_models() == ("qwen3:14b",)
    assert connection.requests[0][0] == ("GET", "/v1/models")
    assert "Authorization" not in connection.requests[0][1]["headers"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": []},
        {"data": None},
        {"data": [None]},
        {"data": [{"id": 123}]},
        {"data": [{"id": "qwen3:14b"}, {"id": "qwen3:14b"}]},
    ],
)
def test_malformed_model_catalogue_fails_closed(monkeypatch, payload):
    client = ollama_selection().inference({})
    monkeypatch.setattr(client, "_request", lambda *a: payload)
    with pytest.raises(ValueError, match="INFERENCE_MODEL_CATALOGUE_INVALID"):
        client.available_models()


def test_probe_overrides_are_bounded_and_cannot_change_auth_or_model():
    selection = ollama_selection()
    client = selection.inference({}, max_output_tokens=512, timeout_seconds=180)
    assert client.configuration.max_output_tokens == 512
    for overrides in (
        {"timeout_seconds": 181},
        {"max_output_tokens": 16001},
        {"model": "unreviewed"},
        {"authentication": "bearer"},
    ):
        with pytest.raises(ValueError):
            selection.inference({}, **overrides)
    with pytest.raises(ValueError, match="INFERENCE_UNUSED_CREDENTIAL_DENIED"):
        replace(client.configuration, api_key="never-use-a-placeholder")


def test_missing_credential_not_required_only_in_explicit_none_mode():
    with pytest.raises(ValueError, match="INFERENCE_CREDENTIAL_MISSING"):
        InferenceConfiguration(
            provider="openai",
            model="pinned",
            endpoint="https://inference.example.test/v1/chat/completions",
        )
