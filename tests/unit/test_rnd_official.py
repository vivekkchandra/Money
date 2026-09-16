"""Offline contract fixtures, never evidence of a successful live provider call."""

from __future__ import annotations

import copy
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import Mock

import pytest

from money.data.rnd_official import (
    BoEMacroProvider,
    FredMacroProvider,
    OnsMacroProvider,
    SecEdgarProvider,
    UKCompanyNumberProvider,
    _sec_throttle,
    collect_macro_context,
    collect_official_context,
)
from money.data.security import FetchResult, ProviderFailure, SafeFetcher, SourceSecurityError

STAMP = datetime(2026, 9, 16, 12, tzinfo=UTC)
AGENT = "Money contract tests test@example.invalid"


class FakeFetcher(SafeFetcher):
    def __init__(self, data: Any = None, content: bytes = b"") -> None:
        super().__init__(frozenset({"data.sec.gov", "www.sec.gov"}), user_agent=AGENT)
        self.data = data
        self.content = content
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def json(self, url: str, *, headers: dict[str, str] | None = None) -> Any:
        self.calls.append((url, headers))
        if isinstance(self.data, Exception):
            raise self.data
        return copy.deepcopy(self.data)

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        mime_types: tuple[str, ...] = ("application/json",),
    ) -> FetchResult:
        self.calls.append((url, headers))
        return FetchResult(content=self.content, mime="text/csv")


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("money.data.rnd_official._sec_throttle", lambda: None)


def submissions() -> dict[str, Any]:
    return {
        "cik": "320193",
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000001"],
                "filingDate": ["2026-09-15"],
                "reportDate": ["2026-06-30"],
                "form": ["10-Q"],
                "primaryDocument": ["report.htm"],
                "acceptanceDateTime": ["2026-09-15T15:01:01Z"],
            }
        },
    }


@pytest.mark.parametrize("agent", ["", "Money", "Money test@example.invalid\r\nX-Evil: yes"])
def test_sec_requires_safe_contact_agent(agent: str) -> None:
    with pytest.raises((ValueError, SourceSecurityError)):
        SecEdgarProvider(agent)


def test_sec_custom_user_agent_is_sent_by_bounded_transport() -> None:
    provider = SecEdgarProvider(AGENT)
    assert provider._fetcher.user_agent == AGENT
    assert provider._fetcher.allowed_hosts == frozenset({"www.sec.gov", "data.sec.gov"})
    assert provider._fetcher.maximum_redirects == 0


def test_sec_injected_transport_must_identify_operator() -> None:
    with pytest.raises(ValueError, match="MISMATCH"):
        SecEdgarProvider(AGENT, fetcher=SafeFetcher(frozenset({"data.sec.gov"})))


def test_sec_directory_search_cik_and_exact_ticker_priority() -> None:
    fetcher = FakeFetcher(
        {
            "fields": ["cik", "name", "ticker", "exchange"],
            "data": [
                [320194, "Apple supplier", "AAPLX", "Nasdaq"],
                [320193, "Apple Inc.", "AAPL", "Nasdaq"],
            ],
        }
    )
    provider = SecEdgarProvider(AGENT, fetcher=fetcher)
    found = provider.search("AAPL", STAMP)
    assert found.records[0].ticker == "AAPL"
    assert found.records[0].cik == "0000320193"
    assert not found.provenance.historical_pit_safe
    assert found.provenance.commercial_rights == "UNAPPROVED"
    assert provider.search("320193", STAMP).records[0].ticker == "AAPL"


def test_sec_shared_rate_gate_denies_before_network() -> None:
    fetcher = FakeFetcher(submissions())
    with pytest.raises(ProviderFailure, match="SEC_RATE_LIMIT"):
        SecEdgarProvider(AGENT, fetcher=fetcher, rate_gate=lambda: False).submissions(320193, STAMP)
    assert not fetcher.calls


def test_sec_local_throttle_spaces_actual_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("money.data.rnd_official._SEC_LAST_REQUEST", 100.0)
    monkeypatch.setattr("money.data.rnd_official.time.monotonic", Mock(side_effect=[100.1, 100.25]))
    sleep = Mock()
    monkeypatch.setattr("money.data.rnd_official.time.sleep", sleep)
    _sec_throttle()
    assert sleep.call_args.args[0] == pytest.approx(0.15)


def test_sec_throttle_contention_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    lock = Mock()
    lock.acquire.return_value = False
    monkeypatch.setattr("money.data.rnd_official._SEC_LOCK", lock)
    with pytest.raises(ProviderFailure, match="SEC_RATE_LIMIT"):
        _sec_throttle()
    lock.acquire.assert_called_once_with(timeout=1)
    lock.release.assert_not_called()


@pytest.mark.parametrize("cik", ["../320193", "0", "12345678901", True])
def test_sec_cik_rejected_before_fetch(cik: Any) -> None:
    fetcher = FakeFetcher()
    with pytest.raises(ValueError, match="CIK_INVALID"):
        SecEdgarProvider(AGENT, fetcher=fetcher).submissions(cik, STAMP)
    assert not fetcher.calls


def test_sec_filings_preserve_aware_acceptance_not_derived_filing_midnight() -> None:
    result = SecEdgarProvider(AGENT, fetcher=FakeFetcher(submissions())).submissions(320193, STAMP)
    assert result.records[0].acceptance_time == datetime(2026, 9, 15, 15, 1, 1, tzinfo=UTC)
    assert result.records[0].filing_date == date(2026, 9, 15)
    assert result.provenance.availability_time == STAMP
    assert result.provenance.historical_pit_safe is False
    data = submissions()
    data["filings"]["recent"]["acceptanceDateTime"] = ["2026-09-15T15:01:01"]
    result = SecEdgarProvider(AGENT, fetcher=FakeFetcher(data)).submissions(320193, STAMP)
    assert result.records[0].acceptance_time is None


@pytest.mark.parametrize(
    "field,value,code",
    [
        ("primaryDocument", ["../../secret.htm"], "PATH_INVALID"),
        ("accessionNumber", ["abc"], "ACCESSION_INVALID"),
        ("filingDate", ["2026-09-17"], "PIT_VIOLATION"),
        ("acceptanceDateTime", ["2026-09-16T13:00:00Z"], "PIT_VIOLATION"),
        ("form", [], "COLUMNS_INVALID"),
    ],
)
def test_sec_filings_fail_closed(field: str, value: Any, code: str) -> None:
    data = submissions()
    data["filings"]["recent"][field] = value
    with pytest.raises(ProviderFailure, match=code):
        SecEdgarProvider(AGENT, fetcher=FakeFetcher(data)).submissions(320193, STAMP)


def test_sec_response_company_mismatch_rejected() -> None:
    data = submissions()
    data["cik"] = "123456"
    with pytest.raises(ProviderFailure, match="COMPANY_MISMATCH"):
        SecEdgarProvider(AGENT, fetcher=FakeFetcher(data)).submissions(320193, STAMP)


def facts() -> dict[str, Any]:
    return {
        "cik": 320193,
        "facts": {
            "us-gaap": {
                "Assets": {
                    "label": "Assets",
                    "units": {
                        "USD": [
                            {
                                "val": 1000,
                                "end": "2025-12-31",
                                "filed": "2026-01-20",
                                "form": "10-K",
                                "accn": "0000320193-26-000001",
                            },
                            {
                                "val": 900,
                                "end": "2025-12-31",
                                "filed": "2026-02-20",
                                "form": "10-K/A",
                                "accn": "0000320193-26-000002",
                            },
                        ]
                    },
                }
            }
        },
    }


def test_sec_facts_keep_restatements_units_and_no_fake_publication() -> None:
    result = SecEdgarProvider(AGENT, fetcher=FakeFetcher(facts())).company_facts(320193, STAMP)
    assert [row.value for row in result.records] == [Decimal(1000), Decimal(900)]
    assert all(row.unit == "USD" for row in result.records)
    assert result.records[0].filing_date != result.records[1].filing_date
    assert result.provenance.historical_pit_safe is False


@pytest.mark.parametrize("value", ["NaN", "Infinity", True, {}, "not-a-number"])
def test_sec_malformed_facts_rejected(value: Any) -> None:
    data = facts()
    data["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] = value
    with pytest.raises(ProviderFailure, match="VALUE_INVALID"):
        SecEdgarProvider(AGENT, fetcher=FakeFetcher(data)).company_facts(320193, STAMP)


def test_boe_csv_preserves_missing_values_and_date_precision() -> None:
    fetcher = FakeFetcher(content=b"DATE,IUDBEDR\n14 Sep 2026,4.0\n15 Sep 2026,..\n")
    result = BoEMacroProvider(fetcher=fetcher).observations(
        date(2026, 9, 1), date(2026, 9, 16), STAMP
    )
    assert result.observations[0].value == Decimal("4.0")
    assert result.observations[1].value is None
    assert result.provenance.hash_basis == "response_bytes"
    assert result.provenance.historical_pit_safe is False
    assert result.unit == "percent"
    assert "SeriesCodes=IUDBEDR" in fetcher.calls[0][0]


@pytest.mark.parametrize(
    "content",
    [
        b"<html>error</html>",
        b"DATE,OTHER\n14 Sep 2026,4\n",
        b"DATE,IUDBEDR\n14 Sep 2026,4\n14 Sep 2026,5\n",
        b"DATE,IUDBEDR\n17 Sep 2026,4\n",
        b"DATE,IUDBEDR\n14 Sep 2026,NaN\n",
    ],
)
def test_boe_malformed_or_conflicting_data_rejected(content: bytes) -> None:
    with pytest.raises(ProviderFailure):
        BoEMacroProvider(fetcher=FakeFetcher(content=content)).observations(
            date(2026, 9, 1), date(2026, 9, 16), STAMP
        )


def test_fred_secret_not_in_provenance_and_missing_not_zero() -> None:
    fetcher = FakeFetcher(
        {
            "count": 1,
            "offset": 0,
            "observations": [
                {
                    "date": "2026-09-01",
                    "value": ".",
                    "realtime_start": "2026-09-16",
                    "realtime_end": "2026-09-16",
                },
            ],
        }
    )
    key = "f" * 32
    result = FredMacroProvider(key, "TEST", fetcher=fetcher).observations(
        date(2026, 9, 1), date(2026, 9, 16), STAMP
    )
    assert key in fetcher.calls[0][0]
    assert key not in result.model_dump_json()
    assert "api_key" not in result.provenance.source_url
    assert result.observations[0].value is None
    assert result.unit is None


def test_fred_demo_key_and_partial_pages_rejected() -> None:
    with pytest.raises(ValueError, match="CREDENTIAL"):
        FredMacroProvider("abcdefghijklmnopqrstuvwxyz123456", "GDP")
    with pytest.raises(ProviderFailure, match="INCOMPLETE"):
        FredMacroProvider(
            "f" * 32,
            "GDP",
            fetcher=FakeFetcher(
                {
                    "count": 20000,
                    "offset": 0,
                    "observations": [],
                }
            ),
        ).observations(date(2026, 9, 1), date(2026, 9, 16), STAMP)


def test_ons_explicit_version_dimensions_period_no_guessed_publication() -> None:
    fetcher = FakeFetcher(
        {
            "dimensions": {
                "geography": {"option": {"id": "K02000001"}},
                "time": {"option": {"id": "Aug-26"}},
            },
            "total_observations": 1,
            "offset": 0,
            "unit_of_measure": "Index: 2015=100",
            "observations": [{"observation": "130.2"}],
        }
    )
    result = OnsMacroProvider(
        "cpih01",
        "time-series",
        6,
        dimensions={"geography": "K02000001"},
        periods={"Aug-26": date(2026, 8, 1)},
        precision="MONTH",
        fetcher=fetcher,
    ).observations(date(2026, 8, 1), date(2026, 9, 16), STAMP)
    assert "/versions/6/observations?" in fetcher.calls[0][0]
    assert result.observations[0].precision == "MONTH"
    assert result.observations[0].value == Decimal("130.2")
    assert not result.provenance.historical_pit_safe


def test_ch_reviewed_number_only_metadata_and_no_secret_leak() -> None:
    fetcher = FakeFetcher(
        {
            "items": [
                {
                    "transaction_id": "abc123",
                    "date": "2026-09-15",
                    "category": "accounts",
                    "description": "accounts-with-accounts-type-full",
                }
            ]
        }
    )
    result = UKCompanyNumberProvider("test-secret", fetcher).filing_index("12345678", STAMP)
    assert result.records[0].company_number == "12345678"
    assert result.provenance.historical_pit_safe is False
    assert "test-secret" not in result.model_dump_json()
    assert fetcher.calls[0][1] and fetcher.calls[0][1]["Authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    "country,kwargs,reason",
    [
        ("US", {}, "SEC_CONTACT_USER_AGENT_REQUIRED"),
        ("GB", {}, "COMPANIES_HOUSE_CREDENTIAL_MISSING"),
        ("GB", {"companies_house_key": "test"}, "COMPANY_NUMBER_MAPPING_MISSING"),
        ("ZZ", {}, "OFFICIAL_COUNTRY_UNSUPPORTED"),
    ],
)
def test_collect_missing_config_never_ready(country: str, kwargs: Any, reason: str) -> None:
    result = collect_official_context("TEST", country, user_agent=None, **kwargs)
    assert result["status"] == "NOT_CONFIGURED"
    assert result["reason"] == reason
    assert result["records"] == []
    assert result["retrieved_at"] is None


def test_macro_collection_no_silent_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    mocked = Mock(side_effect=ProviderFailure("PROVIDER_UNAVAILABLE"))
    monkeypatch.setattr(BoEMacroProvider, "observations", mocked)
    result = collect_macro_context()
    assert result["status"] == "UNAVAILABLE"
    assert result["records"] == []
    assert result["production_qualified"] is False


def test_sec_collection_rejects_unverified_symbol_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    from money.data.rnd_official import OfficialBatch, SecCompany

    provider = SecEdgarProvider(
        AGENT,
        fetcher=FakeFetcher(
            {
                "fields": ["cik", "name", "ticker", "exchange"],
                "data": [[123, "Example Class B", "EX-B", "NYSE"]],
            }
        ),
    )
    result: OfficialBatch[SecCompany] = provider.search("EX", STAMP)
    monkeypatch.setattr(SecEdgarProvider, "search", Mock(return_value=result))
    filings = Mock()
    monkeypatch.setattr(SecEdgarProvider, "submissions", filings)
    context = collect_official_context("EX.B", "US", user_agent=AGENT)
    assert context["status"] == "UNAVAILABLE"
    assert context["reason"] == "SEC_EXACT_TICKER_MATCH_REQUIRED"
    filings.assert_not_called()


def test_collection_errors_do_not_expose_provider_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(SecEdgarProvider, "search", Mock(side_effect=ValueError("secret-content")))
    result = collect_official_context("AAPL", "US", user_agent=AGENT)
    assert result["reason"] == "OFFICIAL_RESPONSE_INVALID"
    assert "secret-content" not in str(result)


def test_future_macro_window_prevents_network() -> None:
    fetcher = FakeFetcher()
    with pytest.raises(ValueError, match="WINDOW_INVALID"):
        BoEMacroProvider(fetcher=fetcher).observations(date(2026, 9, 1), date(2026, 9, 17), STAMP)
    assert not fetcher.calls


def test_ch_company_number_path_injection_prevents_network() -> None:
    fetcher = FakeFetcher()
    with pytest.raises(ValueError, match="MAPPING_INVALID"):
        UKCompanyNumberProvider("test-key", fetcher).filing_index("../../companies", STAMP)
    assert not fetcher.calls


def test_fred_different_vintage_not_silently_accepted() -> None:
    fetcher = FakeFetcher(
        {
            "count": 1,
            "offset": 0,
            "observations": [
                {
                    "date": "2026-09-01",
                    "value": "1",
                    "realtime_start": "2026-09-15",
                    "realtime_end": "2026-09-16",
                },
            ],
        }
    )
    with pytest.raises(ProviderFailure, match="VINTAGE_MISMATCH"):
        FredMacroProvider("f" * 32, "GDP", fetcher=fetcher).observations(
            date(2026, 9, 1), date(2026, 9, 16), STAMP
        )


def test_ons_wrong_dimension_is_rejected() -> None:
    fetcher = FakeFetcher(
        {
            "dimensions": {
                "geography": {"option": {"id": "WRONG"}},
                "time": {"option": {"id": "Aug-26"}},
            },
            "total_observations": 1,
            "offset": 0,
            "unit_of_measure": "Index: 2015=100",
            "observations": [{"observation": "130.2"}],
        }
    )
    with pytest.raises(ProviderFailure, match="DIMENSION_MISMATCH"):
        OnsMacroProvider(
            "cpih01",
            "time-series",
            6,
            dimensions={"geography": "K02000001"},
            periods={"Aug-26": date(2026, 8, 1)},
            precision="MONTH",
            fetcher=fetcher,
        ).observations(date(2026, 8, 1), date(2026, 9, 16), STAMP)
