"""Native-node I/O boundary fixtures; not live runtime qualification evidence."""

from __future__ import annotations

import ast
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from money.adapters import native_qualitative
from money.adapters.upstream import UpstreamUnavailable


def forbidden(*args: object, **kwargs: object) -> str:
    raise AssertionError("Native external provider fetcher must never run")


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    fixture = ModuleType("fixture_native_sentiment")
    fixture.get_news = SimpleNamespace(func=forbidden)  # type: ignore[attr-defined]
    fixture.fetch_stocktwits_messages = forbidden  # type: ignore[attr-defined]
    fixture.fetch_reddit_posts = forbidden  # type: ignore[attr-defined]

    def imports(name: str) -> ModuleType:
        assert name == "tradingagents.agents.analysts.sentiment_analyst"
        return fixture

    monkeypatch.setattr(native_qualitative, "import_module", imports)
    return fixture


def test_prefetch_aliases_are_snapshot_only_and_shared_tool_is_unmodified(module: Any) -> None:
    shared_tool = module.get_news
    originals = {name: getattr(module, name) for name in (
        "get_news", "fetch_stocktwits_messages", "fetch_reddit_posts"
    )}
    with native_qualitative._snapshot_sentiment_sources():
        assert shared_tool.func is forbidden
        for call in (module.get_news.func, module.fetch_stocktwits_messages, module.fetch_reddit_posts):
            result = call("NICL.LSE", start_date="2026-09-01", end_date="2026-09-18")
            assert "<unavailable>" in result
            assert "No observations" in result
            assert "separately provenance-labelled evidence" in result
            assert "do not relabel" in result
    assert all(getattr(module, name) is original for name, original in originals.items())


def test_prefetch_aliases_restore_even_when_native_analysis_fails(module: Any) -> None:
    shared_tool = module.get_news
    with pytest.raises(RuntimeError, match="fixture failure"):
        with native_qualitative._snapshot_sentiment_sources():
            raise RuntimeError("fixture failure")
    assert module.get_news is shared_tool
    assert module.fetch_stocktwits_messages is forbidden
    assert module.fetch_reddit_posts is forbidden


def test_missing_pinned_prefetch_surface_fails_closed(module: Any) -> None:
    del module.fetch_reddit_posts
    with pytest.raises(UpstreamUnavailable, match="sentiment source boundary"):
        with native_qualitative._snapshot_sentiment_sources():
            pytest.fail("incomplete upstream shape was admitted")


def test_actual_pinned_sentiment_node_executes_without_prefetch_or_source_relabelling(
    module: Any,
) -> None:
    """Execute unmodified pinned node functions with explicit fake LLM dependencies.

    AST selection avoids importing missing native dependency packages in the unit
    environment; these fixture executions are never represented as native audit.
    """
    path = Path("upstreams/tradingagents/tradingagents/agents/analysts/sentiment_analyst.py")
    before = path.read_bytes()
    parsed = ast.parse(before, filename=str(path))
    selected = ast.Module(body=[
        node for node in parsed.body
        if isinstance(node, ast.FunctionDef)
        and node.name in {"create_sentiment_analyst", "_seven_days_back", "_build_system_message"}
    ], type_ignores=[])
    assert len(selected.body) == 3
    captured: list[dict[str, Any]] = []

    class Prompt:
        def __init__(self) -> None:
            self.values: dict[str, Any] = {}

        @classmethod
        def from_messages(cls, value: object) -> Prompt:
            return cls()

        def partial(self, **values: Any) -> Prompt:
            self.values.update(values)
            return self

        def format_messages(self, **values: Any) -> dict[str, Any]:
            return self.values | values

    def invoke(*args: Any) -> str:
        captured.append(args[2])
        return "Native sentiment sources are unavailable; use frozen Money evidence only."

    module.__dict__.update({
        "datetime": datetime, "timedelta": timedelta,
        "ChatPromptTemplate": Prompt, "MessagesPlaceholder": lambda **values: values,
        "bind_structured": lambda *args: None, "SentimentReport": object,
        "get_instrument_context_from_state": lambda state: "Frozen Money snapshot",
        "get_language_instruction": lambda: "Fixture language instruction",
        "NO_EXTERNAL_TOOLS": "Fixture no-external-tools policy",
        "invoke_structured_or_freetext": invoke, "render_sentiment_report": object,
        "AIMessage": lambda **values: SimpleNamespace(**values),
    })
    exec(compile(selected, str(path), "exec"), module.__dict__)
    with native_qualitative._snapshot_sentiment_sources():
        result = module.create_sentiment_analyst(object())({
            "company_of_interest": "NICL.LSE", "trade_date": "2026-09-18", "messages": [],
        })
    assert "sentiment_report" in result
    system = captured[0]["system_message"]
    assert system.count("<unavailable>") >= 3
    for source in ("Native news / Yahoo Finance", "StockTwits", "Reddit"):
        assert source + " prefetch is disabled" in system
    assert "do not relabel it as this source" in system
    assert path.read_bytes() == before
    assert module.get_news.func is forbidden
