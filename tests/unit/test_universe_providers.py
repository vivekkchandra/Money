"""Synthetic network fixtures; never production evidence or rights approval."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, parse_qsl, urlsplit

import pytest

from money.data.security import ProviderFailure, SourceSecurityError
from money.qualification.core import QualificationContext
from money.qualification.universe_providers import (
    BulkProviderEnricher,
    qualify_bulk_provider_reports,
)

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def row():
    return {
        "ticker": "FIX",
        "short_ticker": "FIX",
        "trading212_id": "FIXl_EQ",
        "name": "Fixture PLC",
        "isin": "GB00BH4HKS39",
        "quote_currency": "GBX",
        "instrument_type": "STOCK",
        "exchange_id": 123,
        "exchange_name": "Fixture venue",
        "mic": "XLON",
        "country": "GB",
        "observed_at": NOW.isoformat(),
        "qualification_state": "UNRESOLVED_ISA_SCOPE",
        "reasons": ["ISA_REVIEW_REQUIRED"],
    }


class Fetcher:
    def __init__(self):
        self.calls = []
        self.search = [
            {
                "Code": "FIX",
                "Exchange": "LSE",
                "Name": "Fixture PLC",
                "Type": "Common Stock",
                "Country": "UK",
                "Currency": "GBX",
                "ISIN": row()["isin"],
            }
        ]
        self.exchanges = [{"Code": "LSE", "OperatingMIC": "XLON"}]
        self.general = {
            "Code": "FIX",
            "Name": "Fixture PLC",
            "CurrencyCode": "GBX",
            "ISIN": row()["isin"],
            "Type": "Common Stock",
            "CountryISO": "GB",
            "CountryName": "United Kingdom",
            "IsDelisted": False,
            "AddressData": {"Street": "1 Fixture Road", "ZIP": "NE1 1AA"},
            "Description": "Synthetic business description, not approval.",
        }
        self.companies = [
            {
                "company_number": "00000001",
                "title": "FIXTURE PLC",
                "company_status": "active",
                "company_type": "plc",
            }
        ]
        self.profile = {
            "company_number": "00000001",
            "company_name": "FIXTURE PLC",
            "company_status": "active",
            "type": "plc",
            "registered_office_address": {
                "address_line_1": "1 Fixture Road",
                "postal_code": "NE1 1AA",
            },
        }
        self.empty = set()
        self.fail = None

    def json(self, url, *, headers=None):
        parts = urlsplit(url)
        path, query = parts.path, parse_qs(parts.query)
        self.calls.append((path, query))
        if self.fail:
            raise self.fail
        if path in self.empty:
            return []
        if path.startswith("/api/search/"):
            return self.search
        if path == "/api/exchanges-list/":
            return self.exchanges
        if path.startswith("/api/fundamentals/"):
            return self.general
        if path == "/search/companies":
            return {"items": self.companies, "total_results": len(self.companies)}
        if path == "/company/00000001":
            return self.profile
        if path == "/company/00000001/filing-history":
            return {
                "items": [
                    {
                        "transaction_id": "fixture-accounts",
                        "date": "2026-09-15",
                        "category": "accounts",
                        "type": "AA",
                        "description": "accounts-with-accounts-type-full",
                        "description_values": {"made_up_date": "2025-12-31"},
                    }
                ],
                "total_count": 1,
            }
        if path.startswith("/api/splits/"):
            return []
        if path.startswith("/api/eod/"):
            return [
                {
                    "date": "2026-09-15",
                    "open": 100,
                    "high": 110,
                    "low": 90,
                    "close": 105,
                    "volume": 1000,
                }
            ]
        if path.startswith("/api/div/"):
            return [{"date": "2026-06-01", "value": 1, "currency": "GBP"}]
        if path == "/api/news":
            return [
                {
                    "date": "2026-09-15T10:00:00Z",
                    "title": "Fixture news",
                    "link": "https://example.test/story",
                }
            ]
        raise AssertionError(path)


@pytest.fixture
def ctx(tmp_path):
    return QualificationContext(
        tmp_path,
        tmp_path,
        {
            "EODHD_API_KEY": "synthetic-eod-secret-token",
            "COMPANIES_HOUSE_API_KEY": "synthetic-ch-secret-token",
        },
        NOW,
    )


def enricher(ctx, fetcher=None, **kwargs):
    kwargs.setdefault("clock", lambda: ctx.now)
    return BulkProviderEnricher(ctx, fetcher=fetcher or Fetcher(), sleep=lambda _: None, **kwargs)


def test_exact_identity_join_collects_real_probe_contracts_without_approval(ctx):
    fetcher = Fetcher()
    result = enricher(ctx, fetcher).enrich(row())
    assert result["eodhd_symbol"] == "FIX.LSE"
    assert result["companies_house_number"] == "00000001"
    assert result["companies_house_state"] == "MAPPED"
    assert result["identifiers"]["exchange"] == "LSE"
    assert result["eodhd_mapping_attempted"]
    assert result["eodhd_lookup_origin"] == "NETWORK"
    assert set(result["provider_reports"]) == {"eodhd", "companies-house"}
    assert all(item["status"] == "RETRIEVED" for item in result["provider_datasets"].values())
    assert result["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert result["reasons"] == ["ISA_REVIEW_REQUIRED"]
    assert not result["provider_rights_verified"]
    assert not result["financial_documents_verified"]
    assert result["recent_accounts_filings"][0]["transaction_id"] == "fixture-accounts"
    for reference in result["provider_evidence"]:
        raw = (ctx.root / reference["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == reference["sha256"]
    assert not any("orders" in path or "portfolio" in path for path, _ in fetcher.calls)
    assert not any("financial" in key for key in result["provider_datasets"])


@pytest.mark.parametrize(
    "field,value", [("Currency", "GBP"), ("ISIN", "US0378331005"), ("Type", "ETF")]
)
def test_currency_isin_and_type_mismatch_fail_closed(ctx, field, value):
    fetcher = Fetcher()
    fetcher.search[0][field] = value
    result = enricher(ctx, fetcher).enrich(row())
    assert result["eodhd_symbol"] is None
    assert "EODHD_MAPPING_NOT_FOUND" in result["provider_reasons"]
    assert result["provider_reports"] == {}


def test_two_exact_provider_lines_are_ambiguous(ctx):
    fetcher = Fetcher()
    fetcher.search.append(fetcher.search[0] | {"Code": "OTHER"})
    result = enricher(ctx, fetcher).enrich(row())
    assert result["eodhd_symbol"] is None
    assert "EODHD_MAPPING_AMBIGUOUS" in result["provider_reasons"]


@pytest.mark.parametrize(
    "venue",
    [
        {},
        {"mic": None, "country": None, "exchange_id": None, "exchange_name": None},
        {"mic": "", "country": "", "exchange_id": 123, "exchange_name": "Unresolved"},
        {"mic": "XNYS", "country": "US", "exchange_id": 456, "exchange_name": "Foreign"},
    ],
)
def test_venue_is_informational_and_never_prevents_exact_provider_lookup(ctx, venue):
    fetcher = Fetcher()
    source = {
        key: value
        for key, value in row().items()
        if key not in {"mic", "country", "exchange_id", "exchange_name"}
    } | venue
    result = enricher(ctx, fetcher).enrich(source)
    assert result["eodhd_mapping_attempted"]
    assert result["eodhd_symbol"] == "FIX.LSE"
    assert result["identifiers"]["exchange"] == "LSE"
    assert result["companies_house_state"] == "MAPPED"
    assert result["qualification_state"] == "UNRESOLVED_ISA_SCOPE"
    assert not result["provider_rights_verified"]
    assert not any(path == "/api/exchanges-list/" for path, _ in fetcher.calls)


def test_eodhd_exchange_catalogue_is_not_a_mapping_prerequisite(ctx):
    class UnavailableExchangeFetcher(Fetcher):
        def json(self, url, *, headers=None):
            if urlsplit(url).path == "/api/exchanges-list/":
                raise AssertionError("Independent exchange catalogue must not be required")
            return super().json(url, headers=headers)

    result = enricher(ctx, UnavailableExchangeFetcher()).enrich(row() | {"mic": None})
    assert result["eodhd_symbol"] == "FIX.LSE"
    assert set(result["provider_reports"]) == {"eodhd", "companies-house"}


def test_cross_listing_ambiguity_is_not_resolved_by_informational_broker_venue(ctx):
    fetcher = Fetcher()
    fetcher.search.append(fetcher.search[0] | {"Exchange": "OTHER"})
    result = enricher(ctx, fetcher).enrich(row())
    assert result["eodhd_mapping_attempted"]
    assert result["eodhd_symbol"] is None
    assert "EODHD_MAPPING_AMBIGUOUS" in result["provider_reasons"]


def test_company_or_ticker_must_corroborate_exact_isin_lookup(ctx):
    fetcher = Fetcher()
    fetcher.search[0].update(Name="Unrelated issuer", Code="OTHER")
    result = enricher(ctx, fetcher).enrich(row() | {"mic": None})
    assert result["eodhd_symbol"] is None
    assert "EODHD_MAPPING_NOT_FOUND" in result["provider_reasons"]


@pytest.mark.parametrize("field", ["Code", "Exchange"])
def test_provider_symbol_components_must_be_returned_not_inferred(ctx, field):
    fetcher = Fetcher()
    del fetcher.search[0][field]
    result = enricher(ctx, fetcher).enrich(row())
    assert result["eodhd_symbol"] is None
    assert "EODHD_MAPPING_NOT_FOUND" in result["provider_reasons"]


def test_actual_other_uk_venue_suffix_not_inferred_or_hardcoded(ctx):
    fetcher = Fetcher()
    fetcher.search[0]["Exchange"] = "VENUE"
    fetcher.exchanges = [{"Code": "VENUE", "OperatingMIC": "XALT"}]
    result = enricher(ctx, fetcher).enrich(row() | {"mic": "XALT"})
    assert result["eodhd_symbol"] == "FIX.VENUE"
    assert result["provider_datasets"]["eodhd:ohlcv"]["status"] == "RETRIEVED"


def test_foreign_registration_does_not_need_companies_house_even_with_gb_isin(ctx):
    fetcher = Fetcher()
    fetcher.general["CountryISO"] = "IE"
    result = enricher(ctx, fetcher).enrich(row())
    assert result["companies_house_state"] == "NOT_APPLICABLE"
    assert result["companies_house_number"] is None
    assert not any(
        path.startswith("/company") or path.startswith("/search") for path, _ in fetcher.calls
    )
    assert result["eodhd_symbol"] == "FIX.LSE"


def test_foreign_isin_quoted_in_gbx_maps_without_broker_venue(ctx):
    fetcher = Fetcher()
    fetcher.search[0]["ISIN"] = "US0378331005"
    fetcher.general.update(ISIN="US0378331005", CountryISO="US")
    result = enricher(ctx, fetcher).enrich(
        row() | {"isin": "US0378331005", "mic": None, "country": None}
    )
    assert result["eodhd_symbol"] == "FIX.LSE"
    assert result["companies_house_state"] == "NOT_APPLICABLE"
    assert result["companies_house_number"] is None
    assert result["identifiers"]["isin"] == "US0378331005"
    assert result["qualification_state"] == "UNRESOLVED_ISA_SCOPE"


def test_search_country_does_not_imply_issuer_incorporation(ctx):
    fetcher = Fetcher()
    del fetcher.general["CountryISO"]
    result = enricher(ctx, fetcher).enrich(row())
    assert result["companies_house_state"] == "UNRESOLVED_APPLICABILITY"
    assert result["companies_house_number"] is None


@pytest.mark.parametrize("fault", ["ambiguous", "address", "inactive", "operating-company"])
def test_companies_house_ambiguous_and_uncorroborated_identity_fail_closed(ctx, fault):
    fetcher = Fetcher()
    if fault == "ambiguous":
        fetcher.companies.append(fetcher.companies[0] | {"company_number": "00000002"})
    elif fault == "address":
        fetcher.profile["registered_office_address"]["postal_code"] = "OTHER"
    elif fault == "inactive":
        fetcher.profile["company_status"] = "dissolved"
    else:
        fetcher.companies[0]["title"] = "FIXTURE OPERATING COMPANY LIMITED"
    result = enricher(ctx, fetcher).enrich(row())
    assert result["companies_house_state"] == "UNRESOLVED"
    assert result["companies_house_number"] is None
    assert result["eodhd_mapping_state"] == "MAPPED"
    assert not result["provider_rights_verified"]


def test_successful_unchanged_joins_are_resumed_without_network(ctx):
    first = enricher(ctx).enrich(row())
    fetcher = Fetcher()
    fetcher.fail = AssertionError("Must not hit network")
    second = enricher(ctx, fetcher).enrich(row())
    assert second == first | {"eodhd_lookup_origin": "CACHE"}
    assert second["eodhd_mapping_attempted"]
    assert fetcher.calls == []


def test_cache_tampering_is_explicit_and_cannot_produce_admission_proofs(ctx):
    first = enricher(ctx).enrich(row())
    # Model an external corrupted disk, not an edit to production evidence.
    reference = next(
        reference
        for reference in first["provider_evidence"]
        if b'"request"' in (ctx.root / reference["path"]).read_bytes()
    )
    (ctx.root / reference["path"]).write_text("{}")
    fetcher = Fetcher()
    result = enricher(ctx, fetcher).enrich(row())
    assert "PROVIDER_CACHE_INTEGRITY_FAILED" in result["provider_reasons"]
    assert result["eodhd_symbol"] is None
    assert "identifiers" not in result
    assert result["provider_reports"] == {}
    assert result["provider_datasets"] == {}
    assert qualify_bulk_provider_reports(ctx, "eodhd", [result], rights_review(ctx)) is None


def test_expired_cache_does_not_create_current_evidence(ctx):
    enricher(ctx).enrich(row())
    later = QualificationContext(ctx.root, ctx.repo, ctx.environ, NOW + timedelta(days=2))
    fetcher = Fetcher()
    fetcher.fail = ProviderFailure("PROVIDER_TIMEOUT", retryable=True)
    result = enricher(later, fetcher).enrich(row())
    assert result["eodhd_symbol"] is None
    assert result["provider_reports"] == {}


def test_empty_actions_and_news_are_not_qualified(ctx):
    fetcher = Fetcher()
    fetcher.empty = {"/api/div/FIX.LSE", "/api/news"}
    result = enricher(ctx, fetcher).enrich(row())
    assert result["provider_datasets"]["eodhd:corporate_action"]["status"] == "EMPTY"
    assert result["provider_datasets"]["eodhd:news"]["status"] == "EMPTY"
    assert not result["provider_datasets"]["eodhd:news"]["production_qualified"]


def test_secrets_never_persist_even_when_provider_echoes_them(ctx):
    fetcher = Fetcher()
    fetcher.search[0]["Name"] = ctx.environ["EODHD_API_KEY"]
    result = enricher(ctx, fetcher).enrich(row())
    assert result["eodhd_symbol"] is None
    for path in ctx.root.rglob("*.json"):
        raw = path.read_bytes()
        for secret in ctx.environ.values():
            assert secret.encode() not in raw


def test_budget_exhaustion_preserves_partial_progress_and_resumes(ctx):
    fetcher = Fetcher()
    result = enricher(ctx, fetcher, max_requests=1).enrich(row())
    assert len(fetcher.calls) == 1
    assert "PROVIDER_REQUEST_BUDGET_EXHAUSTED" in result["provider_reasons"]
    resumed = enricher(ctx, fetcher).enrich(row())
    assert resumed["eodhd_symbol"] == "FIX.LSE"
    assert sum(path.startswith("/api/search/") for path, _ in fetcher.calls) == 1


def test_budget_skipped_stock_does_not_claim_mapping_attempt(ctx):
    fetcher = Fetcher()
    result = enricher(ctx, fetcher, max_requests=0).enrich(row())
    assert not result["eodhd_mapping_attempted"]
    assert result["eodhd_lookup_origin"] == "NOT_PERFORMED"
    assert fetcher.calls == []


def test_one_bad_instrument_does_not_stop_next_valid_stock(ctx):
    worker = enricher(ctx)
    bad = worker.enrich(row() | {"isin": "malformed"})
    good = worker.enrich(row())
    assert bad["eodhd_symbol"] is None
    assert good["eodhd_symbol"] == "FIX.LSE"


def test_previous_mapping_never_survives_invalid_new_identity(ctx):
    worker = enricher(ctx)
    previous = worker.enrich(row())
    invalid = worker.enrich(previous | {"isin": "invalid"})
    assert "identifiers" not in invalid
    assert "eodhd_identity" not in invalid
    assert invalid["eodhd_symbol"] is None
    assert invalid["companies_house_number"] is None


def test_gbp_mapping_is_distinct_from_gbx_without_unit_guessing(ctx):
    fetcher = Fetcher()
    fetcher.search[0]["Currency"] = "GBP"
    fetcher.general["CurrencyCode"] = "GBP"
    result = enricher(ctx, fetcher).enrich(row() | {"quote_currency": "GBP"})
    assert result["eodhd_symbol"] == "FIX.LSE"
    assert result["identifiers"]["quote_currency"] == "GBP"


def test_rate_limit_waits_are_bounded(ctx):
    pauses = []
    worker = BulkProviderEnricher(
        ctx,
        fetcher=Fetcher(),
        requests_per_minute=30,
        sleep=pauses.append,
        monotonic=lambda: 100.0,
        clock=lambda: ctx.now,
    )
    worker.enrich(row())
    assert pauses and all(pause == 2 for pause in pauses)


def test_transport_cannot_invoke_execution_endpoints(ctx):
    worker = enricher(ctx)
    with pytest.raises(SourceSecurityError, match="READ_ONLY"):
        worker.fetcher.json("https://eodhd.com/api/orders")
    with pytest.raises(SourceSecurityError):
        worker.fetcher.json("https://live.trading212.com/api/v0/equity/orders")


def test_no_credentials_means_no_requests_and_no_fake_failures(ctx):
    ctx.environ = {}
    fetcher = Fetcher()
    result = enricher(ctx, fetcher).enrich(row())
    assert result["provider_reasons"] == ["EODHD_CREDENTIAL_MISSING"]
    assert not result["eodhd_mapping_attempted"]
    assert fetcher.calls == []
    assert json.dumps(result)


def rights_review(ctx, provider="eodhd"):
    ctx.write_bytes("inputs/test-rights.txt", b"Synthetic review bytes, not actual permission")
    return {
        "status": "REVIEWED",
        "rights_evidence_file": "inputs/test-rights.txt",
        "review": {
            "provider": provider,
            "reviewed_by": "Synthetic test reviewer",
            "usage_purpose": "test",
            "storage_policy": "test",
            "redistribution": "PROHIBITED",
            "attribution": "test",
            "source_documentation": "https://example.test/rights",
            "reviewed_at": (NOW - timedelta(hours=1)).isoformat(),
            "valid_until": (NOW + timedelta(days=2)).isoformat(),
        },
    }


def test_aggregate_qualifies_actual_currency_union_without_rewriting_timestamps(ctx):
    first = enricher(ctx).enrich(row())
    later = QualificationContext(
        ctx.root / "later", ctx.repo, ctx.environ, NOW + timedelta(hours=1)
    )
    fetcher = Fetcher()
    fetcher.search[0].update(Code="SECOND", ISIN="US0378331005", Currency="GBP")
    fetcher.general.update(Code="SECOND", ISIN="US0378331005", CurrencyCode="GBP", CountryISO="US")
    second = enricher(later, fetcher).enrich(
        row()
        | {
            "ticker": "SECOND",
            "short_ticker": "SECOND",
            "trading212_id": "SECONDl_EQ",
            "isin": "US0378331005",
            "quote_currency": "GBP",
        }
    )
    assert (
        second["provider_reports"]["eodhd"]["report"]["retrieved_at"]
        != first["provider_reports"]["eodhd"]["report"]["retrieved_at"]
    )
    for path in (later.root / "artifacts").iterdir():
        ctx.write_bytes("artifacts/" + path.name, path.read_bytes())
    ctx.now = later.now
    originals = {
        reference["path"]: (ctx.root / reference["path"]).read_bytes()
        for value in (first, second)
        for reference in value["provider_evidence"]
    }
    qualification = qualify_bulk_provider_reports(ctx, "eodhd", [first, second], rights_review(ctx))
    assert qualification is not None
    assert qualification.currencies == ("GBP", "GBX")
    assert qualification.verified_at == NOW
    assert qualification.valid_until == NOW + timedelta(days=1)
    assert qualification.publication_times == "AS_RETRIEVED"
    assert "financial" not in qualification.datasets
    proof = json.loads(
        ctx.verify_artifact(
            qualification.qualification_report_hash,
            "artifacts/" + qualification.qualification_report_hash + ".json",
        )
    )
    assert len(proof["individual_qualifications"]) == 2
    assert not proof["historical_publication_verified"]
    assert not proof["financial_documents_verified"]
    for path, raw in originals.items():
        assert (ctx.root / path).read_bytes() == raw


def test_aggregate_bad_peer_cannot_expand_currency_coverage(ctx):
    first = enricher(ctx).enrich(row())
    bad = first | {"quote_currency": "GBP"}
    qualification = qualify_bulk_provider_reports(ctx, "eodhd", [bad, first], rights_review(ctx))
    assert qualification is not None
    assert qualification.currencies == ("GBX",)


def test_aggregate_binds_returned_provider_identity_not_informational_broker_mic(ctx):
    observed = enricher(ctx).enrich(row() | {"mic": None})
    rights = rights_review(ctx)
    assert qualify_bulk_provider_reports(ctx, "eodhd", [observed], rights) is not None
    other_venue = observed | {"mic": "XNYS", "country": "US"}
    assert qualify_bulk_provider_reports(ctx, "eodhd", [other_venue], rights) is not None
    wrong_provider_identity = observed | {
        "eodhd_identity": observed["eodhd_identity"] | {"Exchange": "OTHER"}
    }
    assert qualify_bulk_provider_reports(ctx, "eodhd", [wrong_provider_identity], rights) is None


def test_resumed_probe_never_backdates_new_datasets_to_cached_identity_time(ctx):
    incomplete = enricher(ctx, max_requests=3).enrich(row())
    assert incomplete["eodhd_symbol"] == "FIX.LSE"
    ctx.now = NOW + timedelta(hours=2)
    complete = enricher(ctx).enrich(row())
    report = complete["provider_reports"]["eodhd"]["report"]
    assert datetime.fromisoformat(report["retrieved_at"]) == ctx.now
    assert datetime.fromisoformat(complete["identifiers"]["verified_at"]) == NOW
    for dataset in report["datasets"]:
        if dataset["artifact_path"]:
            raw = ctx.verify_artifact(
                dataset["artifact_hash"], "artifacts/" + dataset["artifact_path"]
            )
            records = json.loads(raw)["records"]
            assert all(
                datetime.fromisoformat(record["publication_time"]) == ctx.now for record in records
            )
    qualification = qualify_bulk_provider_reports(ctx, "eodhd", [complete], rights_review(ctx))
    assert qualification is not None
    assert qualification.valid_until == NOW + timedelta(days=1)


def test_aggregate_companies_house_does_not_claim_financial_coverage(ctx):
    observed = enricher(ctx).enrich(row())
    qualification = qualify_bulk_provider_reports(
        ctx, "companies-house", [observed], rights_review(ctx, "companies-house")
    )
    assert qualification is not None
    assert qualification.datasets == ("filing",)


@pytest.mark.parametrize(
    "failure",
    ["unreviewed", "expired-review", "expired-probe", "empty", "wrong-identity", "corrupt"],
)
def test_aggregate_does_not_approve_missing_or_invalid_evidence(ctx, failure):
    fetcher = Fetcher()
    if failure == "empty":
        fetcher.empty.add("/api/news")
    observed = enricher(ctx, fetcher).enrich(row())
    rights = rights_review(ctx)
    if failure == "unreviewed":
        rights["status"] = "UNRESOLVED"
    elif failure == "expired-review":
        rights["review"]["valid_until"] = NOW.isoformat()
    elif failure == "expired-probe":
        ctx.now = NOW + timedelta(days=1)
    elif failure == "wrong-identity":
        observed["trading212_id"] = "DIFFERENTl_EQ"
    elif failure == "corrupt":
        reference = observed["provider_reports"]["eodhd"]["report_ref"]
        (ctx.root / reference["path"]).write_text("{}")
    assert qualify_bulk_provider_reports(ctx, "eodhd", [observed], rights) is None


def test_provider_completion_clock_and_post_io_probe_boundary_are_genuine(ctx):
    clock_value = [NOW]
    completions = {}

    class DelayedFetcher(Fetcher):
        def json(self, url, *, headers=None):
            result = super().json(url, headers=headers)
            clock_value[0] += timedelta(seconds=5)
            parts = urlsplit(url)
            query = tuple(sorted((k, v) for k, v in parse_qsl(parts.query) if k != "api_token"))
            completions[(parts.path, query)] = clock_value[0]
            return result

    result = enricher(ctx, DelayedFetcher(), clock=lambda: clock_value[0]).enrich(row())
    assert result["eodhd_mapping_state"] == "MAPPED"
    raw_receipts = []
    for reference in result["provider_evidence"]:
        value = json.loads(ctx.verify_artifact(reference["sha256"], reference["path"]))
        if value.get("version") == "money-bulk-provider-response-v2":
            raw_receipts.append(value)
            assert (
                datetime.fromisoformat(value["observed_at"])
                == completions[
                    (
                        value["request"]["path"],
                        tuple(tuple(pair) for pair in value["request"]["query"]),
                    )
                ]
            )
    assert raw_receipts
    report = result["provider_reports"]["eodhd"]["report"]
    completed_market = max(
        stamp
        for (path, _), stamp in completions.items()
        if any(part in path for part in ("/eod/", "/div/", "/splits/", "/news"))
    )
    boundary = datetime.fromisoformat(report["retrieved_at"])
    assert boundary >= completed_market > NOW
    assert ctx.now == clock_value[0]
    # Provisional request-start normalizations were never written to disk.
    for path in (ctx.root / "artifacts").glob("*.json"):
        value = json.loads(path.read_bytes())
        if value.get("provider") == "eodhd" and isinstance(value.get("records"), list):
            assert all(
                datetime.fromisoformat(record["publication_time"]) == boundary
                for record in value["records"]
            )


def test_midnight_normalization_cannot_silently_fetch_second_window(ctx):
    clock_value = [datetime(2026, 9, 17, 23, 59, 59, tzinfo=UTC)]
    ctx.now = clock_value[0]

    class MidnightFetcher(Fetcher):
        def json(self, url, *, headers=None):
            result = super().json(url, headers=headers)
            if urlsplit(url).path == "/api/news":
                clock_value[0] += timedelta(seconds=2)
            return result

    fetcher = MidnightFetcher()
    result = enricher(ctx, fetcher, clock=lambda: clock_value[0]).enrich(row())
    report = result["provider_reports"]["eodhd"]["report"]
    assert all(item["status"] == "FAILED" for item in report["datasets"])
    assert sum(path == "/api/news" for path, _ in fetcher.calls) == 1
    assert sum(path == "/api/eod/FIX.LSE" for path, _ in fetcher.calls) == 1
    assert qualify_bulk_provider_reports(ctx, "eodhd", [result], rights_review(ctx)) is None


def test_backwards_provider_clock_fails_closed(ctx):
    result = enricher(ctx, clock=lambda: NOW - timedelta(seconds=1)).enrich(row())
    assert result["eodhd_symbol"] is None
    assert result["provider_reports"] == {}
