"""Fixture-only bridge/HTTP contracts; these tests do not qualify native runtimes."""

from __future__ import annotations

import json
import platform
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import pytest
from test_ollama_inference import StubConnection, StubResponse
from test_ollama_native_integration import analysis as base_analysis

from money.adapters import native_qualitative, native_subprocess
from money.adapters.native import InferenceSession, NativeRunSettings
from money.adapters.native_process import NativeProcessPolicy
from money.adapters.native_qualitative import AIHedgeFundNativeRunner, _native_chat
from money.adapters.upstream import InvalidUpstreamReport, UpstreamUnavailable
from money.crews import cio
from money.research import inference
from money.research.inference import HTTPInference
from money.research.inference_config import InferenceSelection, load_inference_selections
from money.schemas.contracts import (
    EvidenceRecord,
    InstrumentMetadata,
    PriceBar,
    ResearchMandate,
    ResearchSnapshot,
    content_hash,
)

REPO = Path(__file__).resolve().parents[2]


def analysis() -> str:
    return json.dumps(json.loads(base_analysis()) | {"analysis": {
        "price_technical_observations": ["observation"],
        "confidence": "INSUFFICIENT_EVIDENCE",
        "missing_data": ["Synthetic test fixture contains only one price observation."],
    }})


@pytest.fixture
def snapshot() -> ResearchSnapshot:
    now = datetime.now(UTC)
    return ResearchSnapshot(
        snapshot_id="qwen-bridge-fixture", ticker="FIXTURE", created_at=now,
        purpose="RESEARCH_TESTING", usage_mode="PERSONAL_RESEARCH", qlib_enabled=False,
        price_cutoff=now, news_cutoff=now, filing_cutoff=now, fundamental_cutoff=now,
        universe_hash=content_hash({"fixture": True}),
        instrument=InstrumentMetadata(
            ticker="FIXTURE", company="Synthetic bridge fixture", instrument_type="STOCK",
            quote_currency="GBX", currently_available=True, verified_at=now,
            source="fixture", provider="trading212", source_id="FIXTUREl_EQ",
        ),
        evidence=(EvidenceRecord(
            snapshot_id="qwen-bridge-fixture", evidence_id="observed-bar", source="fixture",
            provider="fixture", source_id="observed-bar", canonical_source_id="observed-bar",
            observation_time=now - timedelta(days=1), publication_time=now - timedelta(days=1),
            retrieval_time=now, fresh_until=now + timedelta(days=1), pit_safe=True,
            payload=PriceBar(open=100, high=110, low=95, close=105, volume=1000, currency="GBX"),
        ),),
    )


def selected(role: str) -> InferenceSelection:
    configured = load_inference_selections(
        REPO, {"MONEY_INFERENCE_CONFIG": "data/configuration/ollama-inference.json"}
    )[role]
    # The isolated native child revalidates exactly this JSON selection.
    return InferenceSelection.model_validate_json(configured.model_dump_json())


def capture_http(monkeypatch: pytest.MonkeyPatch, **response_changes: Any) -> list[StubConnection]:
    connections: list[StubConnection] = []

    def connect(address: str, port: int, *, timeout: float) -> StubConnection:
        assert (address, port) == ("127.0.0.1", 11434)
        assert 0 < timeout <= 5
        value = {
            "model": "qwen3:14b",
            "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": analysis(), "reasoning": "PRIVATE_THOUGHT",
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        } | response_changes
        connection = StubConnection(StubResponse(json.dumps(value).encode()))
        connections.append(connection)
        return connection

    monkeypatch.setattr(inference.http.client, "HTTPConnection", connect)
    return connections


def assert_wire_requests(connections: list[StubConnection], count: int) -> None:
    assert len(connections) == count
    for connection in connections:
        assert connection.closed
        assert len(connection.requests) == 1
        (method, path), kwargs = connection.requests[0]
        assert (method, path) == ("POST", "/v1/chat/completions")
        body = json.loads(kwargs["body"])
        assert body["model"] == "qwen3:14b"
        assert body["reasoning_effort"] == "none"
        assert body["temperature"] == 0
        assert body["max_tokens"] == 8000
        assert "max_completion_tokens" not in body
        assert "Authorization" not in kwargs["headers"]
        assert "x-api-key" not in kwargs["headers"]


def test_tradingagents_bridge_sends_configured_qwen_non_thinking_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = capture_http(monkeypatch)
    modules = {
        "langchain_core.runnables": SimpleNamespace(Runnable=object),
        "langchain_core.messages": SimpleNamespace(
            AIMessage=lambda **kwargs: SimpleNamespace(**kwargs)
        ),
    }
    monkeypatch.setattr(native_qualitative, "import_module", modules.__getitem__)
    client = selected("tradingagents").inference({})
    session = InferenceSession(client, NativeRunSettings(), usage_mode="PERSONAL_RESEARCH")
    chat = _native_chat(session, "Frozen fixture evidence")
    bound = chat.bind_tools([])
    result = bound.invoke([SimpleNamespace(type="human", content="Analyse supplied evidence")])
    assert result.content == analysis()
    assert "PRIVATE_THOUGHT" not in result.content
    assert result.tool_calls == []  # Existing snapshot-only bridge semantics are unchanged.
    assert session.calls == 1
    assert_wire_requests(connections, 1)


def test_ai_hedge_fund_personas_send_configured_qwen_non_thinking_payload(
    snapshot: ResearchSnapshot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = capture_http(monkeypatch)
    visited: list[str] = []

    def persona(name: str) -> type:
        class Persona:
            def __init__(self, llm: Any, cache: Any) -> None:
                self.llm = llm

            def get_system_prompt(self) -> str:
                return f"{name} independent philosophy. Signal rules: native response schema"

            def predict(self, ticker: str, date: str, data_client: object) -> Any:
                visited.append(name)
                view = self.build_snapshot(ticker, date, data_client)
                assert view.content_hash == snapshot.hash
                value = self.llm.complete(self.get_system_prompt(), view.render())
                return self._to_signal(ticker, date, self._parse(value), "fixture", view, False)

        return Persona

    modules = {
        "hedge_fund.signals.buffett": SimpleNamespace(BuffettAgent=persona("Buffett")),
        "hedge_fund.signals.lynch": SimpleNamespace(LynchAgent=persona("Lynch")),
    }
    monkeypatch.setattr(native_qualitative, "import_module", modules.__getitem__)
    report = AIHedgeFundNativeRunner(
        selected("ai_hedge_fund").inference({}), NativeRunSettings(verify_source_pin=False)
    )(ResearchMandate(), snapshot)
    assert report.runtime == "demo"  # Scripted modules cannot become qualification evidence.
    assert report.snapshot_hash == snapshot.hash
    assert visited == ["Buffett", "Lynch"]
    assert "PRIVATE_THOUGHT" not in report.model_dump_json()
    assert_wire_requests(connections, 2)


def test_crewai_bridge_sends_configured_qwen_non_thinking_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = capture_http(monkeypatch)

    class BaseLLM:
        def __init__(self, **kwargs: Any) -> None:
            pass

    monkeypatch.setattr(cio, "import_module", lambda name: SimpleNamespace(BaseLLM=BaseLLM))
    client = selected("crewai").inference({})
    session = InferenceSession(client, NativeRunSettings(), usage_mode="PERSONAL_RESEARCH")
    llm = cio._crewai_llm(session)
    assert llm.call("Independently inspect frozen report evidence") == analysis()
    # Reasoning control must not change the pre-existing closed capability boundary.
    with pytest.raises(InvalidUpstreamReport, match="external tools"):
        llm.call("not sent", available_functions={"external_fixture": lambda: None})
    assert_wire_requests(connections, 1)


@pytest.mark.parametrize("role", ["tradingagents", "ai_hedge_fund"])
def test_isolated_request_serialization_keeps_explicit_reasoning_control(
    role: Literal["tradingagents", "ai_hedge_fund"], snapshot: ResearchSnapshot,
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = capture_http(monkeypatch)
    digest = content_hash({"synthetic_boundary_fixture": True})
    monkeypatch.setattr(native_subprocess, "bridge_fingerprint", lambda path: digest)
    interpreter = tmp_path / native_subprocess.NATIVE_ENVIRONMENTS[role] / "bin/python"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    runner = native_subprocess.IsolatedNativeRunner(
        interpreter=interpreter, role=role, repository=tmp_path, selection=selected(role),
        settings=NativeRunSettings(timeout_seconds=5),
        policy=NativeProcessPolicy(gateway_hosts=("127.0.0.1",), gateway_port=11434,
                                   timeout_seconds=5),
        expected_python_version=platform.python_version(), expected_inventory_sha256=digest,
        expected_bridge_sha256=digest, expected_environment_sha256=digest,
    )

    def exchange(argv: list[str], request: bytes, *args: object) -> bytes:
        value = json.loads(request)
        assert value["selection"]["reasoning_effort"] == "none"
        assert value["credential"] is None
        reconstructed = InferenceSelection.model_validate(value["selection"])
        assert isinstance(reconstructed.inference({}), HTTPInference)
        assert reconstructed.inference({}).complete("Fixture policy", "Fixture input") == analysis()
        # No synthetic native report is accepted or emitted as live evidence.
        return json.dumps({
            "protocol": native_subprocess.PROTOCOL, "role": role, "bridge_sha256": digest,
            "python_version": platform.python_version(), "inventory_sha256": digest,
            "environment_sha256": digest, "ok": False, "category": "UpstreamUnavailable",
            "calls": [],
        }).encode()

    monkeypatch.setattr(native_subprocess, "_exchange", exchange)
    with pytest.raises(UpstreamUnavailable):
        runner(ResearchMandate(), snapshot)
    assert_wire_requests(connections, 1)


def test_non_thinking_does_not_turn_model_tool_calls_into_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections = capture_http(monkeypatch, choices=[{
        "finish_reason": "stop", "message": {
            "role": "assistant", "content": "Do not interpret as executed",
            "tool_calls": [{"id": "fixture-call", "type": "function", "function": {
                "name": "fixture_external_tool", "arguments": "{}",
            }}],
        },
    }])
    with pytest.raises(ValueError, match="INFERENCE_TOOL_OUTPUT_DENIED"):
        selected("tradingagents").inference({}).complete("Fixture policy", "Fixture input")
    assert_wire_requests(connections, 1)
