"""Fixture tests do not qualify Yahoo access, licensing, ISA status or PIT safety."""

import copy
import json
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from money.data import rnd_market
from money.data.rnd_market import YFinanceProvider
from money.data.security import ProviderFailure
from money.schemas.contracts import content_hash, utc_now


def search_payload():
    now = utc_now()
    return {
        "requested_at": now.isoformat(),
        "retrieved_at": now.isoformat(),
        "quotes": [
            {
                "symbol": "BARC.L",
                "longname": "Barclays PLC",
                "exchange": "LSE",
                "quoteType": "EQUITY",
            },
            {
                "symbol": "AAPL",
                "longname": "Apple Inc.",
                "exchange": "NMS",
                "quoteType": "EQUITY",
                "currency": "USD",
            },
            {"symbol": "SPY", "longname": "ETF fixture", "exchange": "PCX", "quoteType": "ETF"},
            {
                "symbol": "BTC-USD",
                "longname": "Crypto fixture",
                "exchange": "CCC",
                "quoteType": "CRYPTOCURRENCY",
            },
        ],
    }


def snapshot_payload(ticker="BARC.L", currency="GBp"):
    now = utc_now()
    return {
        "retrieved_at": now.isoformat(),
        "requested_at": (now - timedelta(seconds=1)).isoformat(),
        "provider_version": "fixture-only",
        "metadata": {
            "symbol": ticker,
            "longName": "Real provider shape, synthetic test fixture",
            "exchangeName": "LSE" if ticker.endswith(".L") else "NMS",
            "instrumentType": "EQUITY",
            "currency": currency,
            "regularMarketPrice": 100,
            "regularMarketTime": int((now - timedelta(minutes=10)).timestamp()),
            "exchangeTimezoneName": "Europe/London"
            if ticker.endswith(".L")
            else "America/New_York",
        },
        "info": {
            "symbol": ticker,
            "currency": currency,
            "totalCash": 123456,
            "totalDebt": 10,
            "financialCurrency": "GBP",
            "sector": "<b>Financial Services</b>",
            "industry": "Banking",
            "longBusinessSummary": "<script>bad()</script>Fixture only.",
            "companyOfficers": [{"name": "Do not expose this person"}],
        },
        "history": [
            {
                "timestamp": (now - timedelta(days=3)).isoformat(),
                "open": 98,
                "high": 102,
                "low": 95,
                "close": 100,
                "volume": 12000,
                "dividends": 0,
                "stock_splits": 0,
            },
            {
                "timestamp": (now - timedelta(days=2)).isoformat(),
                "open": 98,
                "high": 102,
                "low": 95,
                "close": 100,
                "volume": 12000,
                "dividends": 2,
                "stock_splits": 0,
            },
            {
                "timestamp": (now - timedelta(days=1)).isoformat(),
                "open": 48,
                "high": 52,
                "low": 45,
                "close": 50,
                "volume": 12000,
                "dividends": 0,
                "stock_splits": 2,
            },
        ],
        "news": [
            {
                "id": "fixture-news",
                "content": {
                    "title": "<b>Fixture</b> headline",
                    "provider": {"displayName": "Fixture publisher"},
                    "pubDate": (now - timedelta(hours=1)).isoformat(),
                    "canonicalUrl": {"url": "https://example.com/article"},
                    "summary": "Restricted full text must never cross the contract",
                },
            }
        ],
    }


def provider(payload, **kwargs):
    return YFinanceProvider(transport=lambda *_: copy.deepcopy(payload), **kwargs)


def test_search_preserves_actual_provider_symbols_and_unknown_currency_eligibility():
    result = provider(search_payload()).search_instruments("Barclays")
    assert [entry.provider_symbol for entry in result.instruments] == ["BARC.L", "AAPL"]
    assert result.instruments[0].canonical_symbol == "BARC"
    assert result.instruments[0].currency == "UNKNOWN"
    assert result.instruments[0].listing_country == "GB"
    assert result.instruments[1].listing_country == "US"
    assert result.instruments[1].currency == "USD"
    assert all(
        item.eligibility == "UNKNOWN" and not item.production_qualified
        for item in result.instruments
    )
    assert result.provider_status == "PERSONAL_RND_ONLY"


@pytest.mark.parametrize(
    "query,limit", [("", 10), (" " * 3, 10), ("x" * 81, 10), ("x\n", 10), ("x", 0), ("x", 21)]
)
def test_bad_search_never_starts_io(query, limit):
    with pytest.raises(ProviderFailure, match="RND_SEARCH_INVALID"):
        YFinanceProvider(transport=lambda *_: pytest.fail("Unexpected I/O")).search(query, limit)


@pytest.mark.parametrize(
    "symbol", ["DEMO.L", "https://127.0.0.1", "A/B", "../etc", "^INDEX", "x" * 33, "AAPL;id"]
)
def test_bad_symbols_cannot_become_native_urls_or_demo(symbol):
    with pytest.raises(ProviderFailure, match="RND_SYMBOL_INVALID"):
        YFinanceProvider(transport=lambda *_: pytest.fail("Unexpected I/O")).snapshot(symbol)


@pytest.mark.parametrize(
    "currency,normalized,normalization",
    [
        ("GBp", Decimal("1"), "GBX_DIVIDE_100"),
        ("GBX", Decimal("1"), "GBX_DIVIDE_100"),
        ("GBP", Decimal("100"), "GBP_IDENTITY"),
        ("USD", None, "NOT_CONVERTED"),
    ],
)
def test_quote_raw_units_and_gbp_normalization_are_not_confused(
    currency, normalized, normalization
):
    payload = snapshot_payload(currency=currency)
    result = provider(payload).snapshot("BARC.L")
    assert result.quote.raw_price == Decimal("100")
    assert result.quote.raw_currency == currency
    assert result.quote.normalized_gbp == normalized
    assert result.quote.normalization == normalization
    assert result.instrument.currency == ("GBX" if currency == "GBp" else currency)


def test_snapshot_retains_corporate_actions_but_excludes_unverified_historical_pit():
    result = provider(snapshot_payload()).snapshot("barc.l")
    assert result.adjustment == "PROVIDER_AS_RETURNED_NO_AUTO_ADJUST"
    assert result.historical_pit_status == "UNVERIFIED_EXCLUDE_FROM_PIT_VALIDATION"
    assert [(item.kind, item.value, item.currency) for item in result.corporate_actions] == [
        ("DIVIDEND", 2, "GBp"),
        ("SPLIT", 2, None),
    ]
    assert all(item.historical_availability == "UNKNOWN" for item in result.corporate_actions)
    assert (
        result.history[0].close == 100
    )  # No hidden division/adjustment applied to historical bars.
    assert result.fundamentals == {"totalCash": 123456.0, "totalDebt": 10.0}
    assert "companyOfficers" not in result.model_dump_json()
    assert result.business_summary == "Fixture only."
    assert result.sector == "Financial Services"
    assert result.news[0].headline == "Fixture headline"
    assert "Restricted full text" not in result.model_dump_json()
    assert result.content_hash == content_hash(
        result.model_dump(mode="json", exclude={"content_hash"})
    )


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda raw: raw["metadata"].update(symbol="AAPL"), "RND_PROVIDER_CONFLICT"),
        (lambda raw: raw["metadata"].update(currency="GBP"), "RND_PROVIDER_CONFLICT"),
        (lambda raw: raw["info"].update(exchange="NMS"), "RND_PROVIDER_CONFLICT"),
        (lambda raw: raw["info"].update(quoteType="ETF"), "RND_PROVIDER_CONFLICT"),
        (lambda raw: raw["metadata"].update(regularMarketPrice=-1), "RND_QUOTE_INVALID"),
        (
            lambda raw: raw["metadata"].update(
                regularMarketTime=int((utc_now() + timedelta(days=1)).timestamp())
            ),
            "RND_QUOTE_INVALID",
        ),
        (lambda raw: raw.update(history=[]), "RND_HISTORY_MISSING"),
        (lambda raw: raw["history"].append(raw["history"][0]), "RND_HISTORY_TIMESTAMP_INVALID"),
        (lambda raw: raw["history"].reverse(), "RND_HISTORY_TIMESTAMP_INVALID"),
        (
            lambda raw: raw["history"][0].update(
                timestamp=(utc_now() + timedelta(days=1)).isoformat()
            ),
            "RND_HISTORY_TIMESTAMP_INVALID",
        ),
        (lambda raw: raw["history"][0].update(high=1), "RND_PROVIDER_DATA_INVALID"),
        (lambda raw: raw["history"][0].update(close=float("nan")), "RND_PROVIDER_DATA_INVALID"),
        (lambda raw: raw["history"][0].update(volume=-1), "RND_PROVIDER_DATA_INVALID"),
    ],
)
def test_malformed_or_conflicting_provider_results_fail_explicitly(mutation, code):
    payload = snapshot_payload()
    mutation(payload)
    with pytest.raises(ProviderFailure, match=code):
        provider(payload).snapshot("BARC.L")


def test_news_deduplicates_and_excludes_unsafe_links_future_content_and_fulltext():
    payload = snapshot_payload()
    article = payload["news"][0]
    unsafe = copy.deepcopy(article)
    unsafe["content"]["canonicalUrl"]["url"] = "https://127.0.0.1/private"
    future = copy.deepcopy(article)
    future["content"]["pubDate"] = (utc_now() + timedelta(days=1)).isoformat()
    payload["news"].extend([copy.deepcopy(article), unsafe, future])
    assert len(provider(payload).snapshot("BARC.L").news) == 1


def test_optional_provider_failures_are_explicit_not_empty_success():
    payload = snapshot_payload()
    payload.update(info={}, news=[], news_unavailable=True, info_unavailable=True)
    result = provider(payload).snapshot("BARC.L")
    assert result.news_status == result.fundamentals_status == "UNAVAILABLE"
    assert result.news == () and result.fundamentals == {}


def test_cache_preserves_retrieval_time_and_callers_cannot_mutate_cached_payload():
    payload = snapshot_payload()
    calls = []
    client = YFinanceProvider(transport=lambda *args: calls.append(args) or payload)
    first = client.snapshot("BARC.L")
    first.fundamentals["fake"] = 10
    second = client.snapshot("BARC.L")
    assert len(calls) == 1
    assert first.retrieved_at == second.retrieved_at
    assert "fake" not in second.fundamentals


def test_concurrent_same_query_coalesces_one_native_attempt():
    started, release = threading.Event(), threading.Event()
    calls = []

    def transport(*args):
        calls.append(args)
        started.set()
        assert release.wait(2)
        return search_payload()

    client = YFinanceProvider(transport=transport)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(client.search, "Barclays") for _ in range(4)]
        assert started.wait(1)
        release.set()
        results = [future.result(2) for future in futures]
    assert len(calls) == 1 and all(result == results[0] for result in results)


def test_circuit_and_local_rate_limits_bound_actual_attempts():
    calls = []

    def failing(*args):
        calls.append(args)
        raise ProviderFailure("RND_PROVIDER_UNAVAILABLE", retryable=True)

    client = YFinanceProvider(transport=failing)
    for _ in range(3):
        with pytest.raises(ProviderFailure, match="RND_PROVIDER_UNAVAILABLE"):
            client.search("Barclays")
    with pytest.raises(ProviderFailure, match="RND_PROVIDER_CIRCUIT_OPEN"):
        client.search("Barclays")
    assert len(calls) == 3
    limited = YFinanceProvider(transport=lambda *_: search_payload(), maximum_calls_per_minute=1)
    limited.search("Barclays")
    with pytest.raises(ProviderFailure, match="RND_PROVIDER_RATE_LIMIT"):
        limited.search("Apple")


def test_health_requires_real_transport_probe_and_never_uses_cached_success():
    calls = []
    payload = snapshot_payload("AAPL", "USD")

    def transport(*args):
        calls.append(args)
        if len(calls) > 1:
            raise ProviderFailure("RND_PROVIDER_UNAVAILABLE")
        return payload

    client = YFinanceProvider(transport=transport)
    assert client.get_quote("AAPL").currency == "USD"
    result = client.health("AAPL")
    assert result.status == "FAILED" and result.error_code == "RND_PROVIDER_UNAVAILABLE"
    assert result.scope == "PERSONAL_RND_ONLY" and len(calls) == 2


def test_quote_identity_is_verified_even_without_snapshot():
    with pytest.raises(ProviderFailure, match="RND_SYMBOL_MISMATCH"):
        provider(snapshot_payload("BARC.L")).get_quote("AAPL")


def test_stale_quotes_fail_the_configured_dead_feed_policy_and_never_claim_realtime():
    payload = snapshot_payload()
    with pytest.raises(ProviderFailure, match="RND_STALE_DATA"):
        provider(payload, maximum_quote_age_seconds=60).snapshot("BARC.L")
    result = provider(payload).snapshot("BARC.L")
    assert result.quote.freshness == "UNKNOWN"
    assert result.quote.maximum_age_seconds == 604800
    assert result.quote.market_timezone == "Europe/London"
    assert result.quote.market_timestamp == result.quote.observed_at
    assert result.quote.requested_at == result.requested_at < result.retrieved_at


@pytest.mark.parametrize("field", ["hash", "ticker", "currency", "chronology"])
def test_saved_snapshot_tampering_is_rejected_even_after_cache_restore(field):
    result = provider(snapshot_payload()).snapshot("BARC.L")
    encoded = result.model_dump(mode="json")
    if field == "hash":
        encoded["fundamentals"]["totalCash"] = 0
    elif field == "ticker":
        encoded["instrument"]["ticker"] = "AAPL"
        encoded["content_hash"] = ""
    elif field == "currency":
        encoded["instrument"]["currency"] = "GBP"
        encoded["content_hash"] = ""
    else:
        encoded["history"].reverse()
        encoded["content_hash"] = ""
    with pytest.raises(ValueError):
        rnd_market.RndMarketSnapshot.model_validate(encoded)


def test_model_copy_cannot_bypass_sealed_hash_revalidation():
    result = provider(snapshot_payload()).snapshot("BARC.L")
    tampered = result.model_copy(update={"fundamentals": {"fake": 123}})
    with pytest.raises(ValueError, match="RND_SNAPSHOT_HASH_MISMATCH"):
        rnd_market.RndMarketSnapshot.model_validate(tampered)


def test_process_deadline_is_enforced_and_environment_excludes_secrets(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "secret-database")
    monkeypatch.setenv("RESEARCH_API_TOKEN", "secret-api")

    def timeout(command, **kwargs):
        assert command[-1] == "--child" and "-I" in command
        assert "shell" not in kwargs
        assert "DATABASE_URL" not in kwargs["env"] and "RESEARCH_API_TOKEN" not in kwargs["env"]
        assert kwargs["timeout"] == 0.1
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(ProviderFailure, match="RND_PROVIDER_TIMEOUT"):
        YFinanceProvider(search_timeout_seconds=0.1).search("Apple")


@pytest.mark.parametrize("stdout", [b"not JSON", b"[]", b"x" * 2_000_001])
def test_oversized_or_invalid_child_response_is_not_accepted(monkeypatch, stdout):
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=stdout)
    )
    with pytest.raises(ProviderFailure):
        YFinanceProvider().search("Apple")


def test_native_history_calls_have_explicit_adjustment_and_timeout_settings(monkeypatch):
    import yfinance

    raw = snapshot_payload()
    seen = []

    class FakeTicker:
        def __init__(self, symbol):
            assert symbol == "BARC.L"

        def history(self, **kwargs):
            seen.append(kwargs)
            return pd.DataFrame(
                {
                    "Open": [98],
                    "High": [102],
                    "Low": [95],
                    "Close": [100],
                    "Volume": [100],
                    "Dividends": [0],
                    "Stock Splits": [0],
                },
                index=pd.to_datetime([utc_now() - timedelta(days=1)]),
            )

        def get_history_metadata(self, **kwargs):
            assert kwargs == {"repair": False}
            return raw["metadata"]

        def get_info(self):
            return raw["info"]

        def get_news(self, **kwargs):
            assert kwargs == {"count": 10, "tab": "news"}
            return raw["news"]

    monkeypatch.setattr(yfinance, "Ticker", FakeTicker)
    result = rnd_market._native("snapshot", {"ticker": "BARC.L", "period": "6mo"}, 10)
    assert seen == [
        {
            "period": "6mo",
            "interval": "1d",
            "auto_adjust": False,
            "back_adjust": False,
            "repair": False,
            "actions": True,
            "keepna": True,
            "rounding": False,
            "timeout": 8,
            "raise_errors": True,
        }
    ]
    assert len(result["history"]) == 1
    assert "companyOfficers" not in json.dumps(result)
