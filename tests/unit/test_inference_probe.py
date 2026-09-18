"""Synthetic HTTP seams verify probe semantics, not production qualification."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from money.data.security import ProviderFailure
from money.research import inference_probe
from money.research.inference import HTTPInference
from money.research.inference_config import InferenceSelection


@pytest.fixture
def selection() -> InferenceSelection:
    return InferenceSelection(
        provider="ollama",
        model="qwen3:14b",
        endpoint="http://127.0.0.1:11434/v1/chat/completions",
        endpoint_scope="local",
        authentication="none",
        reasoning_effort="none",
    )


def response(*, content="OK", model="qwen3:14b"):
    return {
        "model": model,
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning": "private deliberation",
                },
            }
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 2},
    }


def test_probe_records_actual_identity_and_no_reasoning_or_credentials(selection, monkeypatch):
    requests = []

    def request(self, method, path, body, headers):
        requests.append((method, path, body, headers))
        if method == "GET":
            return {"data": [{"id": "qwen3:14b"}]}
        return response()

    monkeypatch.setattr(HTTPInference, "_request", request)
    receipt = inference_probe.probe_selection("tradingagents", selection, {})
    assert receipt.actual_returned_model == "qwen3:14b"
    assert receipt.model_available is True and receipt.server_reachable
    assert receipt.scope == "LOCAL_INFERENCE_ONLY"
    assert receipt.production_qualified is False and receipt.native_runtime_qualified is False
    assert receipt.matches("tradingagents", selection)
    assert "private deliberation" not in receipt.model_dump_json()
    assert "Authorization" not in requests[0][3] and "Authorization" not in requests[1][3]
    assert requests[0][:2] == ("GET", "/v1/models")
    assert requests[1][2]["messages"][1]["content"] == "Reply with exactly OK"
    assert requests[1][2]["max_tokens"] <= 512
    assert requests[1][2]["reasoning_effort"] == "none"
    assert receipt.reasoning_effort == "none"
    assert receipt.finish_reason == "stop"


def test_missing_model_fails_before_any_chat_request(selection, monkeypatch):
    calls = []

    def request(self, method, *args):
        calls.append(method)
        return {"data": [{"id": "different-model"}]}

    monkeypatch.setattr(HTTPInference, "_request", request)
    with pytest.raises(ValueError, match="INFERENCE_MODEL_UNAVAILABLE"):
        inference_probe.probe_selection("ai_hedge_fund", selection, {})
    assert calls == ["GET"]


@pytest.mark.parametrize("code", ["PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"])
def test_unavailable_or_timeout_never_creates_evidence(selection, monkeypatch, code):
    def request(*args):
        raise ProviderFailure(code, retryable=True)

    monkeypatch.setattr(HTTPInference, "_request", request)
    with pytest.raises(ProviderFailure) as failure:
        inference_probe.probe_selection("crewai", selection, {})
    assert inference_probe.safe_probe_error(failure.value) == code


@pytest.mark.parametrize(
    "result",
    [
        response(content=""),
        response(content=" OK "),
        response(model="qwen3:latest"),
        {"unknown": True},
    ],
)
def test_probe_rejects_empty_inexact_wrong_model_and_malformed_response(
    selection, monkeypatch, result
):
    def request(self, method, *args):
        return {"data": [{"id": "qwen3:14b"}]} if method == "GET" else result

    monkeypatch.setattr(HTTPInference, "_request", request)
    with pytest.raises(ValueError):
        inference_probe.probe_selection("crewai", selection, {})


def test_cli_probes_all_roles_and_does_not_promote_local_success(selection, monkeypatch, capsys):
    roles = ("tradingagents", "ai_hedge_fund", "crewai")
    monkeypatch.setattr(
        inference_probe, "load_inference_selections", lambda *args: dict.fromkeys(roles, selection)
    )
    calls = []

    def request(self, method, *args):
        calls.append(method)
        return {"data": [{"id": "qwen3:14b"}]} if method == "GET" else response()

    monkeypatch.setattr(HTTPInference, "_request", request)
    assert inference_probe.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "INFERENCE_PROBE_PASSED"
    assert output["scope"] == "LOCAL_INFERENCE_ONLY" and not output["production_qualified"]
    assert {row["role"] for row in output["receipts"]} == set(roles)
    assert calls == ["GET", "POST"] * 3


def test_cli_errors_never_echo_provider_secret(selection, monkeypatch, capsys):
    monkeypatch.setattr(
        inference_probe,
        "load_inference_selections",
        lambda *args: dict.fromkeys(("tradingagents", "ai_hedge_fund", "crewai"), selection),
    )

    def secret_failure(*args):
        raise RuntimeError("unit-test-private-value")

    monkeypatch.setattr(HTTPInference, "_request", secret_failure)
    assert inference_probe.main() == 2
    output = capsys.readouterr().out
    assert "unit-test-private-value" not in output
    assert len(json.loads(output)["failures"]) == 3


def test_cli_never_prints_secret_accidentally_pasted_in_model(selection, monkeypatch, capsys):
    secret = "unit-test-existing-api-secret"
    monkeypatch.setenv("EXAMPLE_API_KEY", secret)
    selected = InferenceSelection.model_validate(selection.model_dump() | {"model": secret})
    monkeypatch.setattr(
        inference_probe,
        "load_inference_selections",
        lambda *args: dict.fromkeys(("tradingagents", "ai_hedge_fund", "crewai"), selected),
    )

    def unavailable(*args):
        raise ProviderFailure("PROVIDER_UNAVAILABLE", retryable=True)

    monkeypatch.setattr(HTTPInference, "_request", unavailable)
    assert inference_probe.main() == 2
    output = capsys.readouterr().out
    assert secret not in output
    assert json.loads(output)["error"] == "INFERENCE_OUTPUT_UNSAFE"


def test_cached_evidence_schema_rejects_wrong_identity_or_claimed_production(
    selection, monkeypatch
):
    def request(self, method, *args):
        return {"data": [{"id": "qwen3:14b"}]} if method == "GET" else response()

    monkeypatch.setattr(HTTPInference, "_request", request)
    receipt = inference_probe.probe_selection(
        "crewai", selection, {}, observed_at=datetime.now(UTC)
    )
    raw = receipt.model_dump()
    for change in (
        {"actual_returned_model": "wrong"},
        {"production_qualified": True},
        {"model_availability_checked": False},
    ):
        with pytest.raises(ValueError):
            inference_probe.InferenceProbeEvidence.model_validate(raw | change)
