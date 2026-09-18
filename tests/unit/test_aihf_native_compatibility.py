"""Real pinned persona lifecycle, scripted test-only inference; no qualification proof.

These checks exercise the unchanged upstream application code through Money's
existing snapshot/session seam. They do not waive the installed dependency audit.
"""

from __future__ import annotations

import builtins
import json
import socket
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from money.adapters.native_attestation import SOURCE_DIGESTS, source_fingerprint
from money.adapters.native_qualitative import AIHedgeFundNativeRunner
from money.adapters.upstream import UPSTREAM_SHAS, UpstreamUnavailable
from money.research.inference import HTTPInference, InferenceConfiguration
from money.schemas.contracts import (
    EvidenceRecord,
    InstrumentMetadata,
    PriceBar,
    ResearchEnrichment,
    ResearchMandate,
    ResearchSnapshot,
    content_hash,
)

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


@pytest.fixture
def pinned_personas(monkeypatch: pytest.MonkeyPatch) -> Iterator[ModuleType]:
    source = Path(__file__).resolve().parents[2] / "upstreams/ai-hedge-fund"
    package = source / "hedge_fund"
    if not (package / "signals/llm_agent.py").is_file():
        pytest.skip("SKIPPED_EXTERNAL_UNAVAILABLE: exact pinned AI-HF source is absent")
    assert source_fingerprint(package, "hedge_fund") == SOURCE_DIGESTS["hedge_fund"]
    # Do not reuse a mocked or installed package left by another unit test.
    previous = {name: module for name, module in sys.modules.items()
                if name == "hedge_fund" or name.startswith("hedge_fund.")}
    for name in previous:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.syspath_prepend(str(source))
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    original_import = builtins.__import__

    def no_vendor_import(name: str, *args: Any, **kwargs: Any) -> Any:
        # The explicitly supplied Money InferenceSession must not construct a
        # native provider, even if a provider SDK happens to be installed in CI.
        if name.startswith("langchain") or name == "dotenv":
            raise AssertionError("Money persona path attempted a native vendor import")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_vendor_import)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Money persona path attempted a vendor/data capability")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    try:
        lifecycle = import_module("hedge_fund.signals.llm_agent")
        assert lifecycle.__file__ is not None
        assert Path(lifecycle.__file__).resolve() == (package / "signals/llm_agent.py").resolve()
        monkeypatch.setattr(lifecycle, "make_llm", forbidden)
        monkeypatch.setattr(lifecycle, "build_snapshot", forbidden)
        monkeypatch.setattr(import_module("hedge_fund.llm.client"), "make_llm", forbidden)
        yield lifecycle
    finally:
        for name in tuple(sys.modules):
            if name == "hedge_fund" or name.startswith("hedge_fund."):
                sys.modules.pop(name, None)
        sys.modules.update(previous)


@pytest.fixture
def research_snapshot() -> ResearchSnapshot:
    evidence = EvidenceRecord(
        snapshot_id="aihf-compatibility-fixture", evidence_id="fixture-bar",
        source="synthetic-unit-fixture", provider="synthetic-unit-fixture",
        source_id="fixture-bar", canonical_source_id="fixture-bar",
        observation_time=NOW - timedelta(days=1), publication_time=NOW - timedelta(days=1),
        retrieval_time=NOW, fresh_until=NOW + timedelta(days=1), pit_safe=True,
        payload=PriceBar(open=Decimal(100), close=Decimal(105), high=Decimal(110),
                         low=Decimal(95), volume=1000, currency="GBX"),
    )
    return ResearchSnapshot(
        snapshot_id=evidence.snapshot_id, ticker="UNIT_FIXTURE", created_at=NOW,
        purpose="RESEARCH_TESTING", usage_mode="PERSONAL_RESEARCH", qlib_enabled=False,
        universe_hash=content_hash({"synthetic-unit-fixture": True}),
        price_cutoff=NOW, news_cutoff=NOW, filing_cutoff=NOW, fundamental_cutoff=NOW,
        instrument=InstrumentMetadata(
            ticker="UNIT_FIXTURE", company="Synthetic test company", instrument_type="STOCK",
            quote_currency="GBX", verified_at=NOW, currently_available=True,
            source="synthetic-unit-fixture", provider="synthetic-unit-fixture", source_id="fixture",
        ), evidence=(evidence,),
        enrichment=(
            ResearchEnrichment(source="fundamentals", status="ACCESS_DENIED"),
            ResearchEnrichment(source="companies_house", status="NOT_CONFIGURED"),
        ), missing_data=("Fundamentals are unavailable.", "Company filings are unavailable."),
    )


def analysis_reply(persona: str, *, evidence_id: str = "fixture-bar") -> str:
    return json.dumps({
        "conclusion": f"{persona} fixture-only observation, not an investment conclusion.",
        "claims": [{
            "claim_id": "price", "family": "technical", "classification": "INFERENCE",
            "statement": "The fixture bar records a closing price of 105 GBX.",
            "evidence_ids": [evidence_id],
        }],
        "analysis": {
            "price_technical_observations": ["price"],
            "missing_data": ["Fundamentals are unavailable.", "Company filings are unavailable."],
            "uncertainty": ["A single fixture bar is insufficient for a thesis."],
            "confidence": "INSUFFICIENT_EVIDENCE",
        },
    })


def scripted_inference(
    monkeypatch: pytest.MonkeyPatch, replies: list[str],
) -> tuple[HTTPInference, list[tuple[str, str]]]:
    # Keep the genuine local-only transport and its validation; only the network
    # boundary is replaced with explicitly scripted fixture bytes.
    inference = HTTPInference(InferenceConfiguration(
        provider="ollama", model="qwen3:14b", authentication="none", endpoint_scope="local",
        endpoint="http://127.0.0.1:11434/v1/chat/completions",
    ))
    prompts: list[tuple[str, str]] = []

    def fixture_post(body: dict, headers: dict) -> dict:
        assert "Authorization" not in headers
        messages = body["messages"]
        prompts.append((messages[0]["content"], messages[1]["content"]))
        return {
            "model": "qwen3:14b",
            "choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": replies.pop(0),
            }}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }

    monkeypatch.setattr(inference, "_post", fixture_post)
    return inference, prompts


def test_exact_native_personas_use_money_session_without_vendor_or_fundamentals(
    pinned_personas: ModuleType, research_snapshot: ResearchSnapshot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inference, prompts = scripted_inference(monkeypatch, [analysis_reply("Buffett"), analysis_reply("Lynch")])
    before = research_snapshot.model_dump_json()
    report = AIHedgeFundNativeRunner(inference)(ResearchMandate(), research_snapshot)

    assert report.upstream_sha == UPSTREAM_SHAS["ai_hedge_fund"]
    assert report.snapshot_hash == research_snapshot.hash
    assert report.snapshot_id == research_snapshot.snapshot_id
    assert research_snapshot.model_dump_json() == before
    assert len(prompts) == 2
    assert "Warren Buffett" in prompts[0][0]
    assert "Peter Lynch" in prompts[1][0]
    assert "Buffett fixture-only observation" not in prompts[1][1]
    assert {claim.claim_id for claim in report.claims} == {"aihf:buffett:price", "aihf:lynch:price"}
    assert report.analysis is not None
    assert report.analysis.confidence == "INSUFFICIENT_EVIDENCE"
    assert report.analysis.fundamental_observations == ()
    assert report.analysis.price_technical_observations == ("aihf:buffett:price", "aihf:lynch:price")
    for _, prompt in prompts:
        assert '"status": "ACCESS_DENIED"' in prompt
        assert "Fundamentals are unavailable." in prompt
        assert "Do not invent fundamentals" in prompt
    assert not research_snapshot.qlib_enabled
    assert report.usage.input_tokens == 20


@pytest.mark.parametrize("reply", ["not valid JSON", analysis_reply("Buffett", evidence_id="absent")])
def test_real_persona_parse_failure_cannot_manufacture_a_report(
    pinned_personas: ModuleType, research_snapshot: ResearchSnapshot, reply: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inference, prompts = scripted_inference(monkeypatch, [reply])
    with pytest.raises(UpstreamUnavailable, match="failed or abstained"):
        AIHedgeFundNativeRunner(inference)(ResearchMandate(), research_snapshot)
    assert len(prompts) == 1


def test_real_persona_failure_is_not_replaced_by_missing_peer_output(
    pinned_personas: ModuleType, research_snapshot: ResearchSnapshot, monkeypatch: pytest.MonkeyPatch,
) -> None:
    inference, prompts = scripted_inference(monkeypatch, [analysis_reply("Buffett"), "not valid JSON"])
    with pytest.raises(UpstreamUnavailable, match="failed or abstained"):
        AIHedgeFundNativeRunner(inference)(ResearchMandate(), research_snapshot)
    assert len(prompts) == 2
