from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlsplit

import pytest

from money.data.identifiers import InstrumentIdentifiers
from money.data.normalization.prices import normalize_gbp
from money.data.qualification import ProviderQualification
from money.data.quality.market import evaluate_market_quality
from money.data.uk.live import CompaniesHouseProvider, EODHDProvider, Trading212MetadataProvider
from money.flows.research import build_runtime
from money.scanner.technical import calculate_technical
from money.schemas.contracts import EvidenceRecord, PriceBar, ResearchSnapshot, utc_now


def identifiers(now):
    return InstrumentIdentifiers(
        ticker="VOD.L",
        company_name="Vodafone",
        trading212_id="VODl_EQ",
        exchange_ticker="VOD",
        exchange="XLON",
        isin="GB00BH4HKS39",
        quote_currency="GBX",
        companies_house_number="01833679",
        provider_symbols=(("eodhd", "VOD.LSE"),),
        verified_at=now - timedelta(hours=1),
        valid_until=now + timedelta(hours=1),
        source="test verification",
    )


class Fetch:
    def __init__(self, value, *, splits=None):
        self.value, self.calls, self.splits = value, [], splits if splits is not None else []

    def json(self, url, headers=None):
        self.calls.append((url, headers))
        if "/splits/" in urlsplit(url).path:
            return self.splits
        return self.value


def qualification(now):
    return ProviderQualification(
        provider="eodhd",
        datasets=("ohlcv", "news", "corporate_action"),
        earliest_observation=now - timedelta(days=365),
        publication_times="AS_RETRIEVED",
        maximum_age_seconds=3600,
        production_qualified=True,
        qualified_by="test-only",
        qualification_report_hash="a" * 64,
        verified_at=now - timedelta(days=1),
        valid_until=now + timedelta(days=1),
        attribution="EODHD",
        source_documentation="https://eodhd.com/",
    )


def test_identifiers_fail_closed_on_guessing_staleness_and_ambiguity():
    now = utc_now()
    item = identifiers(now)
    assert item.symbol_for("eodhd", now) == "VOD.LSE"
    with pytest.raises(ValueError, match="MISSING"):
        item.symbol_for("unknown", now)
    with pytest.raises(ValueError, match="STALE"):
        item.symbol_for("eodhd", now + timedelta(days=1))
    with pytest.raises(ValueError, match="check digit"):
        InstrumentIdentifiers.model_validate(item.model_dump() | {"isin": "GB00BH4HKS30"})


def test_metadata_fetch_does_not_assert_isa_or_account_access():
    fetch = Fetch(
        [
            {
                "ticker": "VODl_EQ",
                "isin": "GB00BH4HKS39",
                "type": "STOCK",
                "currencyCode": "GBX",
                "unknown": "never expose",
            }
        ]
    )
    result = Trading212MetadataProvider("test-key", "test-secret", fetch).instruments()
    assert len(fetch.calls) == 1 and fetch.calls[0][0].endswith("/metadata/instruments")
    assert "isa_available" not in result[0] and "unknown" not in result[0]


def test_filings_use_retrieval_availability_not_accounting_date():
    now = utc_now()
    fetch = Fetch(
        {
            "items": [
                {"transaction_id": "abc", "date": "2025-01-01", "description": "<b>Accounts</b>"}
            ],
            "total_count": 1,
        }
    )
    result = CompaniesHouseProvider("test-key", fetch).filings(identifiers(now), "snapshot", now)
    assert result[0].publication_time == now
    assert not result[0].available_at(now - timedelta(seconds=1))
    assert result[0].payload.title == "Accounts"


def test_market_keeps_raw_prices_and_excludes_current_unfinished_day():
    now = utc_now()
    day = (now - timedelta(days=1)).date().isoformat()
    row = dict(date=day, open=100, high=110, low=90, close=105, adjusted_close=40, volume=100)
    fetch = Fetch([row, row | {"date": now.date().isoformat()}])
    result = EODHDProvider("real-shaped-test-key", qualification(now), fetch).fetch(
        identifiers(now), "ohlcv", "snapshot", now
    )
    assert len(result) == 1 and result[0].payload.close == 105
    assert result[0].payload.currency == "GBX"
    assert "/splits/VOD.LSE?" in fetch.calls[0][0]
    assert "RAW_OHLC_VOLUME_NO_SPLITS_IN_WINDOW_V1" in result[0].source
    with pytest.raises(ValueError, match="HISTORICAL"):
        qualification(now).require("ohlcv", now, historical=True)


def technical_snapshot(currency="GBX"):
    runtime = build_runtime("demo")
    base = runtime.snapshot_builder(runtime.eligibility.get_instrument_metadata("DEMO.L"))
    now = base.created_at
    rows = []
    for i in range(80):
        value = Decimal(100 + i) + Decimal(i % 5) / 10
        scale = Decimal(100) if currency == "GBP" else Decimal(1)
        rows.append(
            EvidenceRecord(
                snapshot_id=base.snapshot_id,
                evidence_id=str(i),
                source="test",
                provider="test",
                source_id=str(i),
                canonical_source_id=str(i),
                observation_time=now - timedelta(days=80 - i),
                publication_time=now - timedelta(days=80 - i),
                retrieval_time=now,
                fresh_until=now + timedelta(hours=1),
                pit_safe=True,
                critical=True,
                payload=PriceBar(
                    open=value / scale,
                    high=(value + 1) / scale,
                    low=(value - 1) / scale,
                    close=value / scale,
                    volume=1000000 if i == 79 else 100000,
                    currency=currency,
                ),
            )
        )
    return ResearchSnapshot.model_validate(base.model_dump() | {
        "evidence": rows, "hash": "",
        "instrument": base.instrument.model_dump() | {"quote_currency": currency},
    })


def test_talib_actual_indicators_and_currency_equivalence():
    gbx, gbp = (
        calculate_technical(technical_snapshot()),
        calculate_technical(technical_snapshot("GBP")),
    )
    assert gbx.available and dict(gbx.values) == dict(gbp.values)
    assert dict(gbx.values)["relative_volume_20"] == 10
    assert dict(gbx.values)["atr_14_gbp"] > 0
    assert all(r.channel == "technical" and r.evidence_ids for r in gbx.discoveries)


def test_liquidity_fails_without_spread_or_action_coverage():
    snapshot = technical_snapshot()
    quality = evaluate_market_quality(
        snapshot,
        snapshot.created_at,
        spread_bps=None,
        adjustment_basis="RAW",
        corporate_actions_complete=False,
    )
    assert (
        not quality.passed
        and "SPREAD_EVIDENCE_MISSING" in quality.reasons
        and "CORPORATE_ACTION_COVERAGE_UNKNOWN" in quality.reasons
    )


def test_currency_normalization_property():
    for pennies in range(1, 20000, 37):
        raw = Decimal(pennies) / 100
        gbx = normalize_gbp(raw, "GBX")
        assert gbx.gbp == normalize_gbp(raw / 100, "GBP").gbp
        assert gbx.raw_value == raw and gbx.method == "GBX_DIVIDE_100"


@pytest.mark.parametrize("split", ["2/1", "1/10", "1/1"])
def test_raw_ohlc_never_mixed_with_split_adjusted_volume(split):
    now = utc_now()
    day = (now - timedelta(days=1)).date().isoformat()
    fetch = Fetch([], splits=[{"date": day, "split": split}])
    with pytest.raises(ValueError, match="VOLUME_BASIS_UNVERIFIED"):
        EODHDProvider("test-credential", qualification(now), fetch).fetch(
            identifiers(now), "ohlcv", "snapshot", now
        )
    assert len(fetch.calls) == 1, "Fail before consuming inconsistent OHLCV"


def test_price_feed_requires_matching_market_and_action_coverage():
    now = utc_now()
    for change in ({"datasets": ("ohlcv",)}, {"currencies": ("USD",)}, {"geography": ("US",)}, {"instrument_types": ("ETF",)}):
        with pytest.raises(ValueError, match="COVERAGE_MISSING"):
            EODHDProvider("test-credential", qualification(now).model_copy(update=change), Fetch([])).fetch(
                identifiers(now), "ohlcv", "snapshot", now
            )


def test_filings_refuse_stale_company_mapping():
    now = utc_now()
    with pytest.raises(ValueError, match="MAPPING_STALE"):
        CompaniesHouseProvider("test-credential", Fetch({"items": []})).filings(
            identifiers(now), "snapshot", now + timedelta(days=1)
        )


@pytest.mark.parametrize("spread", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_nonfinite_spread_fails_quality_explicitly(spread):
    snapshot = technical_snapshot()
    quality = evaluate_market_quality(snapshot, snapshot.created_at, spread_bps=spread, adjustment_basis="RAW", corporate_actions_complete=True)
    assert not quality.passed and "SPREAD_EVIDENCE_INVALID" in quality.reasons
    assert quality.spread_bps is None


def test_quality_checks_critical_conflict_and_freshness():
    snapshot = technical_snapshot()
    rows = list(snapshot.evidence)
    rows[-1] = EvidenceRecord.model_validate(rows[-1].model_dump() | {"conflicting": True, "fresh_until": snapshot.created_at, "hash": ""})
    changed = ResearchSnapshot.model_validate(snapshot.model_dump() | {"evidence": rows, "hash": ""})
    quality = evaluate_market_quality(changed, snapshot.created_at, spread_bps=Decimal(10), adjustment_basis="RAW", corporate_actions_complete=True)
    assert {"CRITICAL_DATA_CONFLICT", "CRITICAL_DATA_STALE"} <= set(quality.reasons)


def test_technical_scanner_rejects_currency_mismatch_and_invalid_thresholds():
    from money.scanner.technical import ScannerPolicy

    snapshot = technical_snapshot()
    mismatched = ResearchSnapshot.model_validate(snapshot.model_dump() | {"instrument": snapshot.instrument.model_dump() | {"quote_currency": "GBP"}, "hash": ""})
    with pytest.raises(ValueError, match="currency mismatch"):
        calculate_technical(mismatched)
    with pytest.raises(ValueError, match="RSI"):
        ScannerPolicy(rsi_low=80, rsi_high=30)
