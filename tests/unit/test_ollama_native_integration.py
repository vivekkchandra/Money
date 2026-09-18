"""Scripted transport/adapter contracts, never native runtime qualification."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from money.adapters.native import InferenceSession, NativeRunSettings, parse_analysis
from money.adapters.native_qualitative import (
    AIHedgeFundNativeRunner,
    TradingAgentsNativeRunner,
)
from money.adapters.upstream import InvalidUpstreamReport, TradingAgentsAdapter
from money.crews.cio import _crewai_llm
from money.research.inference import HTTPInference, InferenceConfiguration
from money.schemas.contracts import (
    EvidenceRecord,
    InstrumentMetadata,
    PriceBar,
    ResearchMandate,
    ResearchSnapshot,
)


def analysis() -> str:
    return json.dumps(
        {
            "conclusion": "The supplied observation needs further independent research.",
            "claims": [
                {
                    "claim_id": "observation",
                    "family": "technical",
                    "statement": "The supplied closing price is 105 GBX.",
                    "evidence_ids": ["observed-bar"],
                    "classification": "INFERENCE",
                }
            ],
        }
    )


class ScriptedOllama(HTTPInference):
    """Exercise the real shared protocol, replacing only external network I/O."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(
            InferenceConfiguration(
                provider="ollama",
                model="qwen3:14b",
                endpoint="http://127.0.0.1:11434/v1/chat/completions",
                authentication="none",
                endpoint_scope="local",
            )
        )
        self.replies = list(replies)
        self.requests: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []

    def _post(self, body: dict, headers: dict[str, str]) -> dict:
        self.requests.append(body)
        self.headers.append(headers)
        return {
            "model": "qwen3:14b",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": self.replies.pop(0),
                        "reasoning": "PRIVATE_REASONING_MUST_NOT_ENTER_REPORTS",
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10},
        }


@pytest.fixture
def snapshot() -> ResearchSnapshot:
    now = datetime.now(UTC)
    observed = now - timedelta(days=1)
    return ResearchSnapshot(
        snapshot_id="scripted-ollama-adapter-test",
        ticker="TEST.L",
        created_at=now,
        price_cutoff=now,
        news_cutoff=now,
        filing_cutoff=now,
        fundamental_cutoff=now,
        instrument=InstrumentMetadata(
            ticker="TEST.L",
            company="Synthetic fixture",
            instrument_type="STOCK",
            quote_currency="GBX",
            verified_at=now,
            source="test",
            provider="test",
            source_id="test",
            isa_available=True,
            currently_available=True,
            activities_verified=True,
        ),
        evidence=(
            EvidenceRecord(
                snapshot_id="scripted-ollama-adapter-test",
                evidence_id="observed-bar",
                source="test",
                provider="test",
                source_id="observed-bar",
                canonical_source_id="observed-bar",
                observation_time=observed,
                publication_time=observed,
                retrieval_time=now,
                fresh_until=now + timedelta(days=1),
                pit_safe=True,
                payload=PriceBar(
                    open=100, high=110, low=95, close=105, volume=1000, currency="GBX"
                ),
            ),
        ),
    )


def test_tradingagents_retains_analysts_debate_and_risk_with_shared_ollama_transport(
    snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from money.adapters import native_qualitative

    roles = (
        ("create_market_analyst", "market_report"),
        ("create_fundamentals_analyst", "fundamentals_report"),
        ("create_news_analyst", "news_report"),
        ("create_sentiment_analyst", "sentiment_report"),
        ("create_bull_researcher", "investment_plan"),
        ("create_bear_researcher", "investment_plan"),
        ("create_research_manager", "investment_plan"),
        ("create_aggressive_debator", "risk_note"),
        ("create_conservative_debator", "risk_note"),
        ("create_neutral_debator", "risk_note"),
    )
    visited: list[str] = []
    attempted_tools: list[str] = []

    def forbidden_tool() -> None:
        attempted_tools.append("execution")

    def factory(name: str, key: str) -> Any:
        def build(chat: Any) -> Any:
            def invoke(state: dict[str, Any]) -> dict[str, Any]:
                visited.append(name)
                reply = chat.bind_tools([forbidden_tool]).invoke([name])
                assert reply.tool_calls == []
                assert "PRIVATE_REASONING" not in reply.content
                return {key: reply.content}

            return invoke

        return build

    nodes = SimpleNamespace(**{name: factory(name, key) for name, key in roles})

    class Propagator:
        def create_initial_state(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {
                "messages": [],
                "investment_debate_state": {"history": "Own-firm debate only"},
                "risk_debate_state": {"history": "Own-firm risk review only"},
            }

    modules = {
        "langchain_core.runnables": SimpleNamespace(Runnable=object),
        "langchain_core.messages": SimpleNamespace(
            AIMessage=lambda **kwargs: SimpleNamespace(**kwargs)
        ),
        "tradingagents.agents": nodes,
        "tradingagents.graph.propagation": SimpleNamespace(Propagator=Propagator),
        "tradingagents.agents.analysts.sentiment_analyst": SimpleNamespace(
            get_news=SimpleNamespace(func=forbidden_tool),
            fetch_stocktwits_messages=forbidden_tool,
            fetch_reddit_posts=forbidden_tool,
        ),
    }
    monkeypatch.setattr(native_qualitative, "import_module", modules.__getitem__)
    transport = ScriptedOllama(["Own-firm cited observation"] * len(roles) + [analysis()])
    frozen = snapshot.model_dump_json()
    result = TradingAgentsNativeRunner(transport, NativeRunSettings(verify_source_pin=False))(
        ResearchMandate(), snapshot
    )

    assert visited == [name for name, _ in roles]
    assert len(transport.requests) == 11
    assert result.llm_provider_family == "ollama" and result.model_family == "qwen3:14b"
    assert result.runtime == "demo"  # Scripted modules must never acquire live authority.
    assert result.snapshot_hash == snapshot.hash and snapshot.model_dump_json() == frozen
    assert attempted_tools == []
    assert all("Authorization" not in headers for headers in transport.headers)
    assert "PRIVATE_REASONING" not in result.model_dump_json()
    assert all(snapshot.snapshot_id in str(request) for request in transport.requests)
    with pytest.raises(InvalidUpstreamReport, match="fixture output"):
        TradingAgentsAdapter(lambda _mandate, _snapshot: result).research(
            ResearchMandate(), snapshot
        )


def test_aihf_keeps_separate_persona_lifecycles_using_frozen_snapshot_and_ollama(
    snapshot: ResearchSnapshot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from money.adapters import native_qualitative

    visited: list[str] = []

    def persona(name: str) -> type:
        class Persona:
            def __init__(self, llm: Any, cache: Any) -> None:
                self.llm = llm
                self.cache = cache

            def get_system_prompt(self) -> str:
                return f"{name} independent analytical philosophy. Signal rules: omitted"

            def predict(self, ticker: str, date: str, data_client: object) -> Any:
                visited.append(name)
                view = self.build_snapshot(ticker, date, data_client)
                assert view.content_hash == snapshot.hash
                assert not hasattr(data_client, "get_prices")
                raw = self.llm.complete(self.get_system_prompt(), view.render())
                return self._to_signal(ticker, date, self._parse(raw), "key", view, False)

        return Persona

    modules = {
        "hedge_fund.signals.buffett": SimpleNamespace(BuffettAgent=persona("Buffett")),
        "hedge_fund.signals.lynch": SimpleNamespace(LynchAgent=persona("Lynch")),
    }
    monkeypatch.setattr(native_qualitative, "import_module", modules.__getitem__)
    transport = ScriptedOllama([analysis(), analysis()])
    frozen = snapshot.model_dump_json()
    report = AIHedgeFundNativeRunner(transport, NativeRunSettings(verify_source_pin=False))(
        ResearchMandate(), snapshot
    )

    assert visited == ["Buffett", "Lynch"] and len(transport.requests) == 2
    assert report.runtime == "demo" and report.llm_provider_family == "ollama"
    assert {claim.claim_id for claim in report.claims} == {
        "aihf:buffett:observation",
        "aihf:lynch:observation",
    }
    assert snapshot.model_dump_json() == frozen
    assert "PRIVATE_REASONING" not in report.model_dump_json()
    assert all("Authorization" not in headers for headers in transport.headers)


def test_crewai_model_bridge_uses_shared_ollama_content_and_denies_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from money.crews import cio

    class BaseLLM:
        def __init__(self, **kwargs: Any) -> None:
            self.configuration = kwargs

    monkeypatch.setattr(cio, "import_module", lambda _name: SimpleNamespace(BaseLLM=BaseLLM))
    transport = ScriptedOllama(['{"findings":[]}'])
    llm = _crewai_llm(InferenceSession(transport, NativeRunSettings()))

    assert llm.configuration["provider"] == "ollama"
    assert llm.configuration["model"] == "qwen3:14b"
    assert llm.call("Independently audit sealed reports") == '{"findings":[]}'
    assert "Authorization" not in transport.headers[0]
    assert llm.supports_function_calling() is False
    with pytest.raises(InvalidUpstreamReport, match="external tools"):
        llm.call("ignored", available_functions={"place_order": lambda: None})
    assert len(transport.requests) == 1


def test_ollama_output_cannot_bypass_evidence_or_research_only_rules(
    snapshot: ResearchSnapshot,
) -> None:
    for text in (
        analysis().replace("observed-bar", "not-in-snapshot"),
        analysis().replace("The supplied closing price is 105 GBX.", "BUY NOW"),
    ):
        transport = ScriptedOllama([text])
        with pytest.raises(InvalidUpstreamReport):
            parse_analysis(transport.complete("research only", "frozen evidence"), snapshot)
