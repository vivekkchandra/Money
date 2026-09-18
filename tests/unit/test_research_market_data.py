"""Synthetic isolated fixtures, never live market or qualification evidence."""

from datetime import UTC, datetime, timedelta

import pytest

from money.qualification.core import QualificationContext
from money.research.market_data import (
    HistoricalMarketBar,
    HistoricalMarketData,
    MarketDataFailure,
    MarketDataRequest,
    acquire_market_data,
    load_cached_market_data,
    load_research_market_data,
)
from money.schemas.contracts import PriceBar, content_hash

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)


def cache(tmp_path, *, currency="GBX", search=None, prices=None, days_old=0):
    ctx = QualificationContext(tmp_path, tmp_path, {}, NOW)
    row = {
        "trading212_id": "FIXl_EQ", "short_ticker": "FIX", "name": "Fixture PLC",
        "isin": "GB00BH4HKS39", "quote_currency": currency, "provider_evidence": [],
        "eodhd_symbol": "FIX.LSE",
    }
    search = search if search is not None else [{
        "Code": "FIX", "Exchange": "LSE", "ISIN": row["isin"],
        "Currency": currency, "Type": "Common Stock", "Name": "Fixture PLC",
    }]
    prices = prices if prices is not None else [{
        "date": "2026-09-17", "open": 100, "high": 110, "low": 90,
        "close": 105, "volume": 1000,
    }]
    for path, response in [
        ("/api/search/" + row["isin"], search), ("/api/eod/FIX.LSE", prices)
    ]:
        # A current identity lookup need not mean the historical dataset is current.
        observed = NOW if "/search/" in path else NOW - timedelta(days=days_old)
        sha, path = ctx.artifact({
            "version": "money-bulk-provider-response-v2",
            "request": {"host": "eodhd.com", "path": path, "query": []},
            "observed_at": observed.isoformat(),
            "valid_until": (observed + timedelta(days=1)).isoformat(),
            "response": response,
        })
        row["provider_evidence"].append({"sha256": sha, "path": path})
    return ctx, row


@pytest.mark.parametrize("currency", ["GBP", "GBX"])
def test_actual_cached_prices_available_without_company_rights_or_ethics(tmp_path, currency):
    ctx, row = cache(tmp_path, currency=currency)
    result = load_cached_market_data(ctx, row)
    assert result.attempts[0].status == "AVAILABLE"
    assert result.dataset.mapping.symbol == "FIX.LSE"
    assert result.dataset.bars[0].price.currency == currency
    assert result.dataset.bars[0].price.close == 105
    assert not result.dataset.historical_availability_verified
    assert not result.dataset.corporate_actions_complete
    record, = result.dataset.evidence_records("snapshot-fixture")
    assert record.publication_time is None
    assert record.retrieval_time == NOW
    assert not record.pit_safe
    assert record.hash


def test_missing_exact_mapping_does_not_guess_lse_suffix(tmp_path):
    ctx, row = cache(tmp_path, search=[])
    result = load_cached_market_data(ctx, row)
    assert result.dataset is None
    assert result.attempts[0].code == "EXACT_MARKET_MAPPING_REQUIRED"


def test_cross_listing_ambiguity_and_wrong_quote_fail_closed(tmp_path):
    ctx, row = cache(tmp_path, search=[
        {"Code": "FIX", "Exchange": venue, "ISIN": "GB00BH4HKS39",
         "Currency": "GBX", "Type": "Stock", "Name": "Fixture PLC"}
        for venue in ("LSE", "OTHER")
    ])
    assert load_cached_market_data(ctx, row).dataset is None


def test_cached_bar_corruption_is_not_returned(tmp_path):
    ctx, row = cache(tmp_path)
    reference = row["provider_evidence"][-1]
    (ctx.root / reference["path"]).write_bytes(b"corrupted fixture")
    assert load_cached_market_data(ctx, row).dataset is None


def test_duplicate_sessions_and_invalid_ohlcv_fail_closed(tmp_path):
    prices = [{"date": "2026-09-17", "open": 100, "high": 110,
               "low": 90, "close": 105, "volume": 1000}] * 2
    ctx, row = cache(tmp_path, prices=prices)
    assert load_cached_market_data(ctx, row).dataset is None
    ctx, row = cache(tmp_path / "invalid", prices=[prices[0] | {"high": 1}])
    assert load_cached_market_data(ctx, row).dataset is None


def test_expired_history_is_not_refreshed_by_reading_cache(tmp_path):
    ctx, row = cache(tmp_path, days_old=2)
    result = load_cached_market_data(ctx, row)
    assert result.dataset is None
    assert result.attempts[0].status == "STALE"


def test_fallback_uses_configured_order_and_retains_access_denied(tmp_path):
    ctx, row = cache(tmp_path)
    dataset = load_cached_market_data(ctx, row).dataset
    other_mapping = dataset.mapping.model_copy(update={"provider": "alternative"})
    alternative = HistoricalMarketData.model_validate(
        dataset.model_dump() | {"mapping": other_mapping, "hash": ""}
    )
    calls = []

    class Denied:
        provider = "eodhd"

        def history(self, request):
            calls.append(self.provider)
            raise MarketDataFailure("MARKET_HISTORY_ACCESS_DENIED")

    class Available:
        provider = "alternative"

        def history(self, request):
            calls.append(self.provider)
            return alternative

    request = MarketDataRequest(
        trading212_id="FIXl_EQ", currency="GBX", as_of=NOW,
        mappings=(dataset.mapping, other_mapping),
    )
    result = acquire_market_data(request, (Denied(), Available(), Denied()))
    assert calls == ["eodhd", "alternative"]
    assert result.dataset == alternative
    assert [item.status for item in result.attempts] == ["ACCESS_DENIED", "AVAILABLE"]


def test_transport_secrets_are_not_exposed_in_diagnostics(tmp_path):
    secret = "fixture-secret-transport-error-not-a-real-credential"

    class Failed:
        provider = "alternative"

        def history(self, request):
            raise ValueError("url?api_token=" + secret)

    result = acquire_market_data(
        MarketDataRequest(trading212_id="FIXl_EQ", currency="GBP", as_of=NOW), (Failed(),)
    )
    assert secret not in result.model_dump_json()
    assert result.dataset is None


def test_current_retrieval_does_not_claim_pit_or_no_actions(tmp_path):
    ctx, row = cache(tmp_path)
    dataset = load_cached_market_data(ctx, row).dataset
    with pytest.raises(ValueError, match="HISTORICAL_AVAILABILITY_PROOF"):
        HistoricalMarketData.model_validate(
            dataset.model_dump() | {"historical_availability_verified": True, "hash": ""}
        )
    with pytest.raises(ValueError, match="ACTION_COVERAGE"):
        HistoricalMarketData.model_validate(
            dataset.model_dump() | {"adjustment_basis": "RAW_NO_ACTIONS", "hash": ""}
        )


def test_future_price_timestamp_fails_closed(tmp_path):
    ctx, row = cache(tmp_path)
    dataset = load_cached_market_data(ctx, row).dataset
    with pytest.raises(ValueError, match="FUTURE_OBSERVATION"):
        HistoricalMarketData.model_validate(dataset.model_dump() | {
            "hash": "", "bars": [HistoricalMarketBar(
                timestamp=NOW + timedelta(days=1),
                price=PriceBar(open=1, high=2, low=1, close=2, volume=0, currency="GBX"),
            )],
        })
    assert content_hash(dataset) != content_hash(dataset.mapping)


def yahoo_response(*, symbol="FIX.L", company="Fixture PLC", currency="GBp"):
    return {
        "requested_at": (NOW - timedelta(seconds=1)).isoformat(),
        "retrieved_at": NOW.isoformat(),
        "provider_version": "isolated-synthetic-fixture",
        "metadata": {
            "symbol": symbol, "longName": company, "exchangeName": "LSE",
            "instrumentType": "EQUITY", "currency": currency, "regularMarketPrice": 105,
            "regularMarketTime": int((NOW - timedelta(minutes=10)).timestamp()),
        },
        "info": {}, "info_unavailable": True, "news_unavailable": True,
        "history": [{
            "timestamp": (NOW - timedelta(days=1)).isoformat(),
            "open": 100, "high": 110, "low": 90, "close": 105, "volume": 1000,
            "dividends": 0, "stock_splits": 0,
        }],
    }


def configure_yahoo(ctx, row):
    ctx.environ = {"MONEY_USAGE_MODE": "personal_research"}
    row["instrument_type"] = "STOCK"
    ctx.write_json("inputs/research-market-data.json", {
        "providers": ["eodhd", "yfinance"],
        "yfinance_symbols": {row["trading212_id"]: "FIX.L"},
    })


def test_configured_actual_yfinance_adapter_falls_back_without_fundamentals(tmp_path, monkeypatch):
    ctx, row = cache(tmp_path, search=[])
    configure_yahoo(ctx, row)
    calls = []

    def transport(operation, parameters, timeout):
        calls.append((operation, parameters))
        return yahoo_response()

    monkeypatch.setattr("money.data.rnd_market._run_bounded", transport)
    monkeypatch.setattr("money.research.market_data.utc_now", lambda: NOW)
    result = load_research_market_data(ctx, row, allow_network=True)
    assert calls == [("snapshot", {"ticker": "FIX.L", "period": "1y"})]
    assert result.dataset.mapping.provider == "yfinance"
    assert result.dataset.mapping.symbol == "FIX.L"
    assert result.dataset.mapping.isin is None  # This transport never returned ISIN.
    assert result.dataset.bars[0].price.currency == "GBX"
    assert result.dataset.bars[0].price.close == 105
    assert result.dataset.evidence_records("fixture")[0].publication_time is None
    assert not result.dataset.historical_availability_verified
    assert not result.dataset.corporate_actions_complete
    assert [item.status for item in result.attempts] == ["UNAVAILABLE", "AVAILABLE"]
    for proof in result.dataset.proof_refs:
        assert ctx.verify_artifact(proof.sha256, proof.path)
    resumed = load_research_market_data(ctx, row)
    assert resumed.dataset == result.dataset
    assert len(calls) == 1


@pytest.mark.parametrize("change", [
    {"symbol": "OTHER.L"}, {"company": "Different PLC"}, {"currency": "GBP"},
])
def test_configured_yahoo_symbol_is_not_automatic_identity_approval(tmp_path, monkeypatch, change):
    ctx, row = cache(tmp_path, search=[])
    configure_yahoo(ctx, row)
    monkeypatch.setattr("money.data.rnd_market._run_bounded", lambda *args: yahoo_response(**change))
    monkeypatch.setattr("money.research.market_data.utc_now", lambda: NOW)
    result = load_research_market_data(ctx, row, allow_network=True)
    assert result.dataset is None
    assert result.attempts[-1].status == "UNAVAILABLE"


def test_fallback_never_guesses_symbol_or_queries_for_cheap_screen(tmp_path, monkeypatch):
    ctx, row = cache(tmp_path, search=[])
    ctx.environ = {"MONEY_USAGE_MODE": "personal_research"}

    def forbidden(*args):
        raise AssertionError("A cheap screen or unconfigured symbol must not fetch history")

    monkeypatch.setattr("money.data.rnd_market._run_bounded", forbidden)
    result = load_research_market_data(ctx, row, allow_network=True)
    assert result.attempts[-1].status == "NOT_CONFIGURED"
    configure_yahoo(ctx, row)
    result = load_research_market_data(ctx, row)  # Explicit symbol, but no network permission.
    assert result.dataset is None
    assert result.attempts[-1].status == "UNAVAILABLE"


def test_configured_order_and_valid_eodhd_cache_do_not_spend_fallback_calls(tmp_path, monkeypatch):
    ctx, row = cache(tmp_path)
    configure_yahoo(ctx, row)

    def forbidden(*args):
        raise AssertionError("A valid primary cache should avoid fallback requests")

    monkeypatch.setattr("money.data.rnd_market._run_bounded", forbidden)
    result = load_research_market_data(ctx, row, allow_network=True)
    assert result.dataset.mapping.provider == "eodhd"
    assert len(result.attempts) == 1
    ctx.write_json("inputs/research-market-data.json", {
        "providers": ["yfinance", "eodhd"], "yfinance_symbols": {},
    })
    result = load_research_market_data(ctx, row, allow_network=True)
    assert [item.provider for item in result.attempts] == ["yfinance", "eodhd"]
    assert result.dataset.mapping.provider == "eodhd"


def test_yahoo_fallback_cannot_be_used_as_commercial_source(tmp_path, monkeypatch):
    ctx, row = cache(tmp_path, search=[])
    configure_yahoo(ctx, row)
    ctx.environ = {"MONEY_USAGE_MODE": "hosted_commercial_production"}

    def forbidden(*args):
        raise AssertionError("Research fallback must not be activated for commercial use")

    monkeypatch.setattr("money.data.rnd_market._run_bounded", forbidden)
    result = load_research_market_data(ctx, row, allow_network=True)
    assert result.dataset is None
    assert result.attempts[-1].status == "NOT_CONFIGURED"
