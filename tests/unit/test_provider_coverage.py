from datetime import timedelta

import pytest

from money.data.providers import ProviderCoverage, UKXBRLAdapter
from money.flows.research import build_runtime
from money.schemas.contracts import utc_now


def test_ticker_presence_does_not_imply_dataset_or_historical_coverage():
    now = utc_now()
    instrument = build_runtime("demo").eligibility.get_instrument_metadata("DEMO.L")
    assert instrument is not None
    coverage = ProviderCoverage(
        provider="uk-price-only",
        datasets=("ohlcv",),
        currencies=("GBP", "GBX"),
        instrument_types=("STOCK",),
        earliest_observation=now - timedelta(days=100),
        latest_observation=now,
        historical_publication_times=False,
        production_qualified=True,
    )
    assert coverage.supports(instrument, "ohlcv", now, historical=False, production=True)
    assert not coverage.supports(instrument, "financial", now, historical=False, production=True)
    assert not coverage.supports(instrument, "ohlcv", now, historical=True, production=True)
    yahoo = coverage.model_copy(update={"provider": "yfinance"})
    assert not yahoo.supports(instrument, "ohlcv", now, historical=False, production=True)
    assert yahoo.supports(instrument, "ohlcv", now, historical=False, production=False)


def test_xbrl_parser_bounds_archive_before_invoking_converter():
    called = False

    def converter(archive, source, snapshot):
        nonlocal called
        called = True
        return ()

    adapter = UKXBRLAdapter(converter, maximum_archive_bytes=4)
    with pytest.raises(ValueError):
        adapter.parse(b"oversized", "source", "snapshot")
    assert not called
