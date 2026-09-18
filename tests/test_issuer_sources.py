"""Only genuine exact corroboration can replace an unavailable issuer profile."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from money.data.identifiers import InstrumentIdentifiers
from money.data.security import FetchResult, ProviderFailure, SafeFetcher, SourceSecurityError
from money.qualification.core import QualificationContext
from money.qualification.issuer_sources import (
    API_HOST,
    EXCHANGE_HOST,
    PUBLIC_HOST,
    REGULATOR_HOST,
    IssuerSourceSelection,
    apply_issuer_sources,
    main,
    prepare_issuer_evidence,
    validated_issuer_documents,
)

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
ISIN = "GB0006389398"


def selection() -> IssuerSourceSelection:
    return IssuerSourceSelection(
        isin=ISIN, company_number="00238303", legal_name="NICHOLS PLC", ticker="NICL",
        official_issuer_host="issuer.example",
        issuer_identity_urls=("https://issuer.example/investors",),
        security_identity_url=f"https://{EXCHANGE_HOST}/stock/NICL/nichols-plc/company-page",
        business_disclosure_urls=("https://issuer.example/group",),
    )


def row() -> dict:
    identifiers = InstrumentIdentifiers(
        ticker="NICL", company_name="Nichols", trading212_id="NICLl_EQ",
        exchange_ticker="NICL", exchange="LSE", isin=ISIN, quote_currency="GBX",
        provider_symbols=(("eodhd", "NICL.LSE"),), verified_at=NOW,
        valid_until=NOW + timedelta(days=1), source="Fixture provider identity",
    )
    return {
        "isin": ISIN, "identity_valid": True, "universe_member": True,
        "instrument_type": "STOCK", "quote_currency": "GBX", "trading212_id": "NICLl_EQ",
        "eodhd_symbol": "NICL.LSE", "eodhd_mapping_state": "MAPPED",
        "eodhd_identity": {"ISIN": ISIN, "Code": "NICL", "Exchange": "LSE", "Currency": "GBX", "Type": "Common Stock"},
        "identifiers": identifiers.model_dump(mode="json"),
    }


class Fetcher(SafeFetcher):
    def __init__(self) -> None:
        self.calls = []
        self.responses = {
            "https://issuer.example/investors": b"<p>NICHOLS PLC 00238303 NICL Registered in England &amp; Wales</p>",
            selection().security_identity_url: b"<h1>NICHOLS PLC NICL</h1><p>ISIN GB0006389398</p>",
            f"https://{API_HOST}/company/00238303": b'{"company_name":"NICHOLS PLC","company_number":"00238303","company_status":"active","type":"plc","jurisdiction":"england-wales"}',
            f"https://{API_HOST}/company/00238303/filing-history?category=accounts&items_per_page=4": b'{"items":[]}',
            f"https://{PUBLIC_HOST}/company/00238303": b"<h1>NICHOLS PLC</h1><p>Company number 00238303</p><dl>Company status Active Company type Public limited Company</dl>",
            "https://issuer.example/group": b"<p>NICHOLS PLC 00238303</p><p>This fixture document describes group-wide business evidence, not an ethical approval.</p>",
        }

    def get(self, url, *, headers=None, mime_types=()):
        self.calls.append((url, headers))
        if url not in self.responses:
            raise ProviderFailure("PROVIDER_UNAVAILABLE")
        return FetchResult(self.responses[url], mime_types[0])


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("money.qualification.issuer_sources.utc_now", lambda: NOW)
    ctx = QualificationContext(tmp_path, Path.cwd(), {"COMPANIES_HOUSE_API_KEY": "secret-test-key"}, NOW)
    fetcher = Fetcher()
    receipt = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    return ctx, fetcher, receipt


def test_exact_company_fallback_without_fundamentals(prepared):
    ctx, fetcher, receipt = prepared
    candidate = row()
    assert apply_issuer_sources(ctx, candidate)
    assert candidate["companies_house_number"] == "00238303"
    assert candidate["issuer_facts"]["Jurisdiction"] == "england-wales"
    assert candidate["issuer_facts"]["CountryISO"] == "GB"
    assert receipt["rights_approved"] is False
    assert receipt["ethical_result"] is None
    assert receipt["financial_qualified"] is False
    assert all("fundamentals" not in url for url, _ in fetcher.calls)


@pytest.mark.parametrize("field,value", [
    ("isin", "GB00B63QSB39"), ("trading212_id", "WRONGl_EQ"),
    ("eodhd_symbol", "NICL.US"), ("identity_valid", False),
    ("quote_currency", "GBP"), ("instrument_type", "ETF"),
])
def test_identity_conflicts_do_not_mutate_row(prepared, field, value):
    ctx, _, _ = prepared
    candidate = row()
    candidate[field] = value
    before = deepcopy(candidate)
    assert not apply_issuer_sources(ctx, candidate)
    assert candidate == before


@pytest.mark.parametrize("bad", [
    b"NICHOLS UK PLC 00238303 NICL Registered in England & Wales",
    b"NICHOLS PLC 99999999 NICL Registered in England & Wales",
    b"NICHOLS PLC 00238303 WRONG Registered in England & Wales",
    b"NICHOLS PLC 00238303 NICL",
])
def test_no_fuzzy_name_or_guessed_jurisdiction(prepared, bad):
    ctx, fetcher, _ = prepared
    fetcher.responses["https://issuer.example/investors"] = bad
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", {})
    prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert not apply_issuer_sources(ctx, row())


def test_official_business_evidence_keeps_raw_hash_and_unknown_publication(prepared):
    ctx, _, _ = prepared
    docs = validated_issuer_documents(ctx, row())
    assert len(docs) == 1
    doc = docs[0]
    assert doc["provider"] == "official-issuer"
    assert doc["published_at"] is None
    assert doc["publication_time_established"] is False
    assert ctx.verify_artifact(doc["raw_sha256"], doc["raw_path"])
    assert b"fixture document" in ctx.read_bytes(doc["text_path"])
    assert "PASS" not in str(doc)


def test_corrupted_and_expired_source_are_not_replayed(prepared):
    ctx, _, receipt = prepared
    ref = receipt["identity_sources"][0]
    ctx.write_bytes(ref["path"], b"changed")
    assert not apply_issuer_sources(ctx, row())
    assert validated_issuer_documents(ctx, row()) == []


def test_expired_source_is_not_refreshed_by_reading(prepared):
    ctx, _, _ = prepared
    ctx.now += timedelta(days=1)
    assert not apply_issuer_sources(ctx, row())


def test_missing_receipt_and_document_do_not_clear_ethics(tmp_path):
    ctx = QualificationContext(tmp_path, Path.cwd(), {}, NOW)
    assert not apply_issuer_sources(ctx, row())
    assert validated_issuer_documents(ctx, row()) == []


def test_public_companies_house_profile_can_corroborate_identity(prepared):
    ctx, fetcher, _ = prepared
    ctx.environ = {}
    prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    candidate = row()
    assert apply_issuer_sources(ctx, candidate)
    assert "provider_qualifications" not in candidate
    assert "provider_evidence" not in candidate


def test_only_company_api_receives_key_and_no_secrets_persisted(prepared):
    ctx, fetcher, _ = prepared
    for url, headers in fetcher.calls:
        assert bool(headers) == url.startswith("https://" + API_HOST)
        assert all(part not in url for part in ("orders", "positions", "portfolio", "balance"))
    for path in ctx.root.rglob("*"):
        if path.is_file():
            assert b"secret-test-key" not in path.read_bytes()


def test_resume_reuses_current_captured_bytes(prepared):
    ctx, fetcher, receipt = prepared
    fetcher.calls.clear()
    second = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert fetcher.calls == []
    assert second == receipt


@pytest.mark.parametrize("field,value", [
    ("company_number", "99999999"), ("company_name", "NICHOLS OPERATING PLC"),
    ("company_status", "dissolved"), ("jurisdiction", "scotland"), ("type", "ltd"),
])
def test_registry_conflicts_fail_closed(prepared, field, value):
    import json

    ctx, fetcher, _ = prepared
    url = f"https://{API_HOST}/company/00238303"
    profile = json.loads(fetcher.responses[url])
    profile[field] = value
    fetcher.responses[url] = json.dumps(profile).encode()
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", {})
    prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert not apply_issuer_sources(ctx, row())


def test_official_security_must_contain_exact_isin(prepared):
    ctx, fetcher, _ = prepared
    fetcher.responses[selection().security_identity_url] = b"NICHOLS PLC NICL"
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", {})
    prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert not apply_issuer_sources(ctx, row())


def test_network_failure_is_not_qualification_or_signature(prepared):
    ctx, fetcher, _ = prepared
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", {})
    fetcher.responses.clear()
    result = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert result["status"] == "CAPTURE_INCOMPLETE"
    assert result["rights_approved"] is False
    assert result["ethical_result"] is None
    assert "reviewed_by" not in str(result)
    assert "reviewed_at" not in str(result)
    assert not apply_issuer_sources(ctx, row())


@pytest.mark.parametrize("url", [
    "http://issuer.example/a", "https://localhost/a", "https://issuer.example/a?token=secret",
    "https://user:password@issuer.example/a",
])
def test_source_locations_are_bounded(url):
    with pytest.raises(ValueError):
        IssuerSourceSelection.model_validate({**selection().model_dump(), "issuer_identity_urls": [url]})


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_capture_retains_http_status_without_exception_details(prepared, monkeypatch, status):
    ctx, fetcher, receipt = prepared
    receipt.pop("company_source")
    receipt.pop("filing_source")
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", receipt)
    calls = []

    def denied(url, **kwargs):
        calls.append(url)
        raise ProviderFailure(
            "secret-test-key: never report exception messages",
            http_status=status, retryable=status == 429 or status >= 500,
        )

    monkeypatch.setattr(fetcher, "get", denied)
    result = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert len(calls) == 2
    assert all(url.startswith(f"https://{API_HOST}/") for url in calls)
    assert result["status"] == "CAPTURE_INCOMPLETE"
    assert {failure["source_role"] for failure in result["failures"]} == {"company_source", "filing_source"}
    for failure in result["failures"]:
        assert failure["http_status"] == status
        assert failure["retryable"] is (status == 429 or status >= 500)
        assert failure["code"] == "SOURCE_CAPTURE_UNAVAILABLE"
    assert "secret-test-key" not in str(result)
    for key in ("identity_sources", "business_sources", "security_source"):
        assert result[key] == receipt[key]


@pytest.mark.parametrize("error,code,retryable", [
    (ProviderFailure("PROVIDER_TIMEOUT", retryable=True), "PROVIDER_TIMEOUT", True),
    (ProviderFailure("PROVIDER_DNS_UNAVAILABLE", retryable=True), "PROVIDER_DNS_UNAVAILABLE", True),
    (SourceSecurityError("SOURCE_MIME_DENIED"), "SOURCE_MIME_DENIED", False),
    (SourceSecurityError("SOURCE_ADDRESS_DENIED"), "SOURCE_ADDRESS_DENIED", False),
    (SourceSecurityError("SOURCE_REDIRECT_LIMIT"), "SOURCE_REDIRECT_LIMIT", False),
    (SourceSecurityError("secret-test-key"), "SOURCE_CAPTURE_UNAVAILABLE", False),
    (ValueError("secret-test-key"), "SOURCE_CAPTURE_UNAVAILABLE", False),
    (OSError("secret-test-key"), "SOURCE_CAPTURE_IO_ERROR", False),
])
def test_capture_error_details_are_allowlisted(prepared, monkeypatch, error, code, retryable):
    ctx, fetcher, receipt = prepared
    receipt.pop("company_source")
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", receipt)

    def failed(url, **kwargs):
        raise error

    monkeypatch.setattr(fetcher, "get", failed)
    result = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert len(result["failures"]) == 1
    assert result["failures"][0]["code"] == code
    assert result["failures"][0]["http_status"] is None
    assert result["failures"][0]["retryable"] is retryable
    assert "secret-test-key" not in str(result)
    assert not apply_issuer_sources(ctx, row())


def test_resume_retries_only_missing_sources_and_preserves_success_timestamps(prepared):
    ctx, fetcher, receipt = prepared
    original_company = receipt.pop("company_source")
    receipt["failures"] = [{"source_url": original_company["url"], "code": "SOURCE_CAPTURE_UNAVAILABLE"}]
    receipt["status"] = "CAPTURE_INCOMPLETE"
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", receipt)
    fetcher.calls.clear()
    result = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert [url for url, _ in fetcher.calls] == [original_company["url"]]
    assert result["status"] == "CAPTURED"
    assert result["failures"] == []
    for key in ("identity_sources", "business_sources", "security_source", "filing_source"):
        assert result[key] == receipt[key]
    assert result["rights_approved"] is False
    assert result["ethical_result"] is None


def test_cli_prints_safe_failure_details(prepared, monkeypatch, capsys):
    import json

    ctx, fetcher, receipt = prepared
    receipt.pop("company_source")
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", receipt)

    def denied(url, **kwargs):
        raise ProviderFailure("PROVIDER_UNAVAILABLE", http_status=401)

    monkeypatch.setattr(fetcher, "get", denied)
    monkeypatch.setattr("money.qualification.issuer_sources.SafeFetcher", lambda *args, **kwargs: fetcher)
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "secret-test-key")
    assert main(["--root", str(ctx.root), "--selection", f"inputs/issuer-sources/{ISIN}.json"]) == 2
    output = capsys.readouterr().out
    result = json.loads(output)
    assert result["failed_sources"] == 1
    assert result["failures"][0]["http_status"] == 401
    assert result["failures"][0]["source_role"] == "company_source"
    assert result["rights_approved"] is False
    assert "secret-test-key" not in output
    assert "Authorization" not in output


def use_official_disclosures(ctx, selected=None):
    ctx.environ = {**ctx.environ, "MONEY_ISSUER_SOURCE_POLICY": "official_disclosures"}
    selected = selected or selection()
    ctx.write_json(f"inputs/issuer-sources/{ISIN}.json", selected.model_dump(mode="json"))
    return selected


def test_official_disclosures_never_requests_companies_house_even_with_key(prepared):
    ctx, fetcher, original = prepared
    selected = use_official_disclosures(ctx)
    fetcher.calls.clear()
    result = prepare_issuer_evidence(ctx, selected, fetcher=fetcher)
    assert result["status"] == "CAPTURED"
    assert fetcher.calls == []
    assert "company_source" not in result
    assert "filing_source" not in result
    assert result["identity_sources"] == original["identity_sources"]
    candidate = row()
    assert apply_issuer_sources(ctx, candidate)
    assert candidate["issuer_identity_state"] == "VERIFIED"
    assert candidate["issuer_company_number"] == "00238303"
    assert candidate["issuer_facts"]["Jurisdiction"] == "england-wales"
    assert "companies_house_state" not in candidate
    assert "company_status" not in candidate
    assert "provider_qualifications" not in candidate
    assert result["rights_approved"] is False
    assert result["ethical_result"] is None
    assert result["financial_qualified"] is False


def test_official_disclosures_without_registry_secret_or_artifact(prepared):
    ctx, fetcher, _ = prepared
    use_official_disclosures(ctx)
    ctx.environ = {"MONEY_ISSUER_SOURCE_POLICY": "official_disclosures"}
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", {})
    fetcher.calls.clear()
    result = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert result["status"] == "CAPTURED"
    assert all(API_HOST not in url and PUBLIC_HOST not in url and headers is None
               for url, headers in fetcher.calls)
    assert apply_issuer_sources(ctx, row())
    assert len(validated_issuer_documents(ctx, row())) == 1


def test_regulator_exact_security_identity_joins_current_issuer_ticker(prepared):
    ctx, fetcher, _ = prepared
    selected = IssuerSourceSelection.model_validate({
        **selection().model_dump(),
        "security_identity_url": f"https://{REGULATOR_HOST}/artefacts/NSM/RNS/5160174.html",
    })
    use_official_disclosures(ctx, selected)
    fetcher.responses[selected.security_identity_url] = b"<p>Nichols plc ISIN GB0006389398</p>"
    prepare_issuer_evidence(ctx, selected, fetcher=fetcher)
    assert apply_issuer_sources(ctx, row())
    fetcher.responses["https://issuer.example/investors"] = b"NICHOLS PLC 00238303 Registered in England & Wales"
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", {})
    prepare_issuer_evidence(ctx, selected, fetcher=fetcher)
    assert not apply_issuer_sources(ctx, row())


def test_issuer_host_security_disclosure_is_supported(prepared):
    ctx, fetcher, _ = prepared
    selected = IssuerSourceSelection.model_validate({
        **selection().model_dump(), "security_identity_url": "https://issuer.example/share-information",
    })
    use_official_disclosures(ctx, selected)
    fetcher.responses[selected.security_identity_url] = b"NICHOLS PLC NICL GB0006389398"
    prepare_issuer_evidence(ctx, selected, fetcher=fetcher)
    assert apply_issuer_sources(ctx, row())


@pytest.mark.parametrize("url", [
    "https://data.fca.org.uk/arbitrary/5160174.html",
    "https://data.fca.org.uk/artefacts/NSM/RNS/5160174.html?token=secret",
    "https://untrusted.example/nichols",
])
def test_security_disclosure_host_and_path_are_strict(url):
    with pytest.raises(ValueError):
        IssuerSourceSelection.model_validate({**selection().model_dump(), "security_identity_url": url})


@pytest.mark.parametrize("missing", ["NICHOLS PLC", "00238303", "NICL", "Registered in England &amp; Wales"])
def test_official_disclosures_still_require_exact_company_corroboration(prepared, missing):
    ctx, fetcher, _ = prepared
    use_official_disclosures(ctx)
    fetcher.responses["https://issuer.example/investors"] = fetcher.responses[
        "https://issuer.example/investors"
    ].replace(missing.encode(), b"")
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", {})
    prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    before = row()
    candidate = deepcopy(before)
    assert not apply_issuer_sources(ctx, candidate)
    assert candidate == before


def test_official_disclosures_expiry_and_hash_checks_remain(prepared):
    ctx, fetcher, _ = prepared
    use_official_disclosures(ctx)
    result = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert apply_issuer_sources(ctx, row())
    ctx.now += timedelta(days=1)
    assert not apply_issuer_sources(ctx, row())
    ctx.now = NOW
    ctx.write_bytes(result["security_source"]["path"], b"changed identity")
    assert not apply_issuer_sources(ctx, row())


def test_legacy_receipt_is_readable_only_for_legacy_route(prepared):
    ctx, _, receipt = prepared
    receipt["version"] = "money-authoritative-issuer-sources-v1"
    receipt.pop("issuer_source_policy")
    receipt["selection"].pop("linked_disclosure_urls")
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", receipt)
    assert apply_issuer_sources(ctx, row())
    use_official_disclosures(ctx)
    assert not apply_issuer_sources(ctx, row())


def test_official_policy_is_not_silently_used_for_legacy_consumer(prepared):
    ctx, fetcher, _ = prepared
    use_official_disclosures(ctx)
    prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    ctx.environ = {}
    assert not apply_issuer_sources(ctx, row())


@pytest.mark.parametrize("linked", [True, False])
def test_external_disclosure_requires_actual_link_from_official_site(prepared, linked):
    ctx, fetcher, _ = prepared
    url = "https://documents.example/reports/annual.html"
    selected = IssuerSourceSelection.model_validate({
        **selection().model_dump(), "business_disclosure_urls": [url],
        "linked_disclosure_urls": {url: "https://issuer.example/reports"},
    })
    use_official_disclosures(ctx, selected)
    fetcher.responses["https://issuer.example/reports"] = (
        f'<a href="{url}">Annual report</a>'.encode() if linked else b"No report link"
    )
    fetcher.responses[url] = b"NICHOLS PLC 00238303 Group business description"
    fetcher.calls.clear()
    result = prepare_issuer_evidence(ctx, selected, fetcher=fetcher)
    if linked:
        assert result["status"] == "CAPTURED"
        assert len(validated_issuer_documents(ctx, row())) == 1
        ref = result["link_sources"][0]
        ctx.write_bytes(ref["path"], b"edited official link")
        assert validated_issuer_documents(ctx, row()) == []
    else:
        assert result["status"] == "CAPTURE_INCOMPLETE"
        assert not any(request == url for request, _ in fetcher.calls)
        assert validated_issuer_documents(ctx, row()) == []


def test_missing_security_corroboration_is_not_hidden_by_captured_status(prepared):
    ctx, fetcher, _ = prepared
    use_official_disclosures(ctx)
    fetcher.responses[selection().security_identity_url] = b"<script>Application shell</script>"
    ctx.write_json(f"state/issuer-sources/{ISIN}.json", {})
    result = prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    assert result["status"] == "CAPTURED"
    assert not apply_issuer_sources(ctx, row())
    assert result["ethical_result"] is None


@pytest.mark.parametrize("conflict", [
    {"issuer_facts": {"Jurisdiction": "scotland"}},
    {"issuer_facts": {"CountryISO": "US"}},
    {"issuer_company_number": "99999999"},
])
def test_official_route_does_not_overwrite_conflicting_existing_identity(prepared, conflict):
    ctx, fetcher, _ = prepared
    use_official_disclosures(ctx)
    prepare_issuer_evidence(ctx, selection(), fetcher=fetcher)
    candidate = {**row(), **conflict}
    before = deepcopy(candidate)
    assert not apply_issuer_sources(ctx, candidate)
    assert candidate == before


@pytest.mark.parametrize("document_id", ["5160174", "b924d8c9-c1d1-4c25-aaf4-cee148092499"])
def test_regulatory_business_disclosure_uses_verified_issuer_chain(prepared, document_id):
    ctx, fetcher, _ = prepared
    url = f"https://{REGULATOR_HOST}/artefacts/NSM/RNS/{document_id}.html"
    selected = IssuerSourceSelection.model_validate({
        **selection().model_dump(), "business_disclosure_urls": [url],
    })
    use_official_disclosures(ctx, selected)
    fetcher.responses[url] = b"<h1>Nichols plc</h1><p>Consolidated interim results and segment information.</p>"
    receipt = prepare_issuer_evidence(ctx, selected, fetcher=fetcher)
    documents = validated_issuer_documents(ctx, row())
    assert receipt["status"] == "CAPTURED"
    assert len(documents) == 1
    assert documents[0]["provider"] == "official-issuer"
    assert documents[0]["source_authority"] == "official-regulatory"
    assert documents[0]["published_at"] is None
    assert receipt["financial_qualified"] is False
    assert receipt["ethical_result"] is None
    candidate = row()
    candidate["eodhd_identity"]["ISIN"] = "GB00B63QSB39"
    assert validated_issuer_documents(ctx, candidate) == []


def test_regulatory_business_document_exact_legal_name_still_required(prepared):
    ctx, fetcher, _ = prepared
    url = f"https://{REGULATOR_HOST}/artefacts/NSM/RNS/5160174.html"
    selected = IssuerSourceSelection.model_validate({
        **selection().model_dump(), "business_disclosure_urls": [url],
    })
    use_official_disclosures(ctx, selected)
    fetcher.responses[url] = b"<h1>Nichols UK plc</h1><p>Consolidated results.</p>"
    prepare_issuer_evidence(ctx, selected, fetcher=fetcher)
    assert validated_issuer_documents(ctx, row()) == []


def test_legacy_business_document_still_requires_company_number(prepared):
    ctx, fetcher, _ = prepared
    url = f"https://{REGULATOR_HOST}/artefacts/NSM/RNS/5160174.html"
    selected = IssuerSourceSelection.model_validate({
        **selection().model_dump(), "business_disclosure_urls": [url],
    })
    ctx.write_json(f"inputs/issuer-sources/{ISIN}.json", selected.model_dump(mode="json"))
    fetcher.responses[url] = b"<h1>Nichols plc</h1><p>Consolidated results.</p>"
    prepare_issuer_evidence(ctx, selected, fetcher=fetcher)
    assert apply_issuer_sources(ctx, row())
    assert validated_issuer_documents(ctx, row()) == []


@pytest.mark.parametrize("path", [
    "/arbitrary/5160174.html", "/artefacts/NSM/RNS/untrusted.html",
    "/artefacts/NSM/RNS/b924d8c9-c1d1-4c25-aaf4.html",
    "/artefacts/NSM/RNS/b924d8c9-c1d1-4c25-aaf4-cee148092499.html?token=secret",
])
def test_regulatory_business_source_paths_are_bounded(path):
    with pytest.raises(ValueError):
        IssuerSourceSelection.model_validate({
            **selection().model_dump(), "business_disclosure_urls": [f"https://{REGULATOR_HOST}{path}"],
        })
