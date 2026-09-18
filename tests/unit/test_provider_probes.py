"""Synthetic admission-probe tests; their artifacts are not production evidence."""

import hashlib
import json
from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from test_instrument_catalogue import NOW, fixture_entry

from money.data.provider_probes import (
    ProviderAdmissionReview,
    QualificationArtifacts,
    probe_companies_house,
    probe_eodhd,
    qualify_personal_probe,
    qualify_probe,
)
from money.data.security import ProviderFailure
from money.usage_policy import UsageMode


def test_personal_technical_probe_is_real_proof_not_a_rights_approval(tmp_path):
    artifacts = QualificationArtifacts(tmp_path, secrets=("synthetic-api-secret",))
    report = probe_eodhd("synthetic-api-secret", (sample(),), NOW, artifacts, fetcher=Fetcher())
    qualification = qualify_personal_probe(report, artifacts, clock=lambda: NOW)
    qualification.require("ohlcv", NOW, usage_mode=UsageMode.PERSONAL_RESEARCH)
    assert qualification.personal_use.rights_status == "UNVERIFIED_PERSONAL_USE"
    assert qualification.qualified_by is None
    assert qualification.production_qualified is False
    assert qualification.redistribution == "PROHIBITED"
    assert not qualification.personal_use.external_sharing
    proof = artifacts.read(
        qualification.qualification_report_hash, qualification.qualification_report_hash + ".json"
    )
    assert hashlib.sha256(proof).hexdigest() == qualification.qualification_report_hash
    with pytest.raises(ValueError):
        qualification.require("ohlcv", NOW)
    with pytest.raises(ValueError, match="HISTORICAL"):
        qualification.require("ohlcv", NOW, usage_mode=UsageMode.PERSONAL_RESEARCH, historical=True)
    assert report.rights_review_hash is None


@pytest.mark.parametrize("failure", ["empty", "failed", "stale", "corrupt"])
def test_personal_rights_policy_never_weakens_technical_probe(tmp_path, failure):
    artifacts = QualificationArtifacts(tmp_path, secrets=())
    fetcher = Fetcher(**{failure: "news"}) if failure in {"empty", "failed"} else Fetcher()
    report = probe_eodhd("synthetic-key", (sample(),), NOW, artifacts, fetcher=fetcher)
    if failure == "corrupt":
        (tmp_path / report.datasets[0].artifact_path).write_bytes(b"{}")
    checked = NOW + timedelta(days=1) if failure == "stale" else NOW
    with pytest.raises(ValueError):
        qualify_personal_probe(report, artifacts, clock=lambda: checked)


def sample():
    return fixture_entry().identifiers.model_copy(
        update={
            "companies_house_number": "00000001",
            "provider_symbols": (("eodhd", "FIXTURE.LSE"),),
        }
    )


class Fetcher:
    def __init__(self, failed=None, empty=None):
        self.failed, self.empty, self.paths = failed, empty, []

    def json(self, url, *, headers=None):
        path = urlsplit(url).path
        self.paths.append(path)
        if self.failed and self.failed in path:
            raise ProviderFailure("PROVIDER_UNAVAILABLE")
        if self.empty and self.empty in path:
            return []
        if "/splits/" in path:
            return []
        if "/eod/" in path:
            return [
                {
                    "date": "2026-09-15",
                    "open": 100,
                    "high": 105,
                    "low": 95,
                    "close": 101,
                    "volume": 100,
                }
            ]
        if "/div/" in path:
            return [{"date": "2026-06-10", "value": 1, "currency": "GBP"}]
        if path == "/api/news":
            return [
                {
                    "date": "2026-09-15T11:00:00Z",
                    "title": "Test only",
                    "link": "https://example.test/news",
                }
            ]
        if path.endswith("/filing-history"):
            return {
                "items": [
                    {
                        "transaction_id": "test-filing",
                        "date": "2026-09-15",
                        "description": "Test accounts",
                    }
                ],
                "total_count": 1,
            }
        if path == "/company/00000001":
            return {
                "company_number": "00000001",
                "company_name": "Test Company",
                "registered_office_address": {"test": "Excluded"},
            }
        raise AssertionError(path)


def rights(provider):
    raw = b"Synthetic test review, not licensing evidence"
    return raw, ProviderAdmissionReview(
        provider=provider,
        reviewed_by="Test only",
        usage_purpose="test",
        storage_policy="test",
        redistribution="PROHIBITED",
        attribution="Test only",
        source_documentation="https://example.test",
        rights_evidence_hash=hashlib.sha256(raw).hexdigest(),
        reviewed_at=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(days=2),
    )


def test_access_probe_preserves_as_retrieved_pit_and_writes_actual_hashes(tmp_path):
    artifacts = QualificationArtifacts(tmp_path, secrets=("synthetic-api-secret",))
    report = probe_eodhd("synthetic-api-secret", (sample(),), NOW, artifacts, fetcher=Fetcher())
    assert all(item.status == "RETRIEVED" for item in report.datasets)
    assert not report.historical_publication_verified
    raw, review = rights("eodhd")
    qualification = qualify_probe(report, review, raw, artifacts, clock=lambda: NOW)
    assert qualification.production_qualified
    assert qualification.currencies == ("GBX",)
    assert qualification.publication_times == "AS_RETRIEVED"
    assert qualification.earliest_observation.date().isoformat() == "2026-09-15"
    saved_report = tmp_path / (qualification.qualification_report_hash + ".json")
    assert (
        hashlib.sha256(saved_report.read_bytes()).hexdigest()
        == qualification.qualification_report_hash
    )
    for item in report.datasets:
        assert (
            hashlib.sha256((tmp_path / item.artifact_path).read_bytes()).hexdigest()
            == item.artifact_hash
        )
        records = json.loads((tmp_path / item.artifact_path).read_bytes())["records"]
        assert all(
            record["publication_time"] == NOW.isoformat().replace("+00:00", "Z")
            for record in records
        )


@pytest.mark.parametrize("mode", ["failed", "empty"])
def test_news_failure_or_empty_response_cannot_qualify_news(tmp_path, mode):
    artifacts = QualificationArtifacts(tmp_path, secrets=("synthetic-api-secret",))
    report = probe_eodhd(
        "synthetic-api-secret", (sample(),), NOW, artifacts, fetcher=Fetcher(**{mode: "news"})
    )
    raw, review = rights("eodhd")
    with pytest.raises(ValueError, match="DATASET_NOT_QUALIFIED"):
        qualify_probe(report, review, raw, artifacts, clock=lambda: NOW)


def test_companies_house_does_not_claim_financial_document_coverage(tmp_path):
    artifacts, fetcher = (
        QualificationArtifacts(tmp_path, secrets=("synthetic-api-secret",)),
        Fetcher(),
    )
    report = probe_companies_house(
        "synthetic-api-secret", (sample(),), NOW, artifacts, fetcher=fetcher
    )
    raw, review = rights("companies-house")
    qualification = qualify_probe(report, review, raw, artifacts, clock=lambda: NOW)
    assert qualification.datasets == ("filing",)
    assert not report.financial_documents_verified
    assert fetcher.paths == ["/company/00000001", "/company/00000001/filing-history"]
    assert (
        b"registered_office_address"
        not in (tmp_path / report.datasets[0].artifact_path).read_bytes()
    )


def test_secret_echo_is_never_written(tmp_path):
    artifacts = QualificationArtifacts(tmp_path, secrets=("synthetic-api-secret",))
    with pytest.raises(ValueError, match="SECRET_DETECTED"):
        artifacts.save({"echo": "synthetic-api-secret"})
    assert not tuple(tmp_path.iterdir())


def test_probe_cannot_admit_without_fresh_hash_matching_rights_review(tmp_path):
    artifacts = QualificationArtifacts(tmp_path, secrets=("synthetic-api-secret",))
    report = probe_eodhd("synthetic-api-secret", (sample(),), NOW, artifacts, fetcher=Fetcher())
    raw, review = rights("eodhd")
    with pytest.raises(ValueError, match="REVIEW_INVALID"):
        qualify_probe(report, review, b"changed", artifacts, clock=lambda: NOW)
    with pytest.raises(ValueError, match="REVIEW_INVALID"):
        qualify_probe(
            report,
            review.model_copy(update={"valid_until": NOW}),
            raw,
            artifacts,
            clock=lambda: NOW,
        )


@pytest.mark.parametrize(
    "change", ["missing", "corrupted", "wrong_count", "wrong_ticker", "duplicate"]
)
def test_admission_reverifies_exact_artifact_bytes_and_sample_coverage(tmp_path, change):
    artifacts = QualificationArtifacts(tmp_path, secrets=("synthetic-api-secret",))
    report = probe_eodhd("synthetic-api-secret", (sample(),), NOW, artifacts, fetcher=Fetcher())
    first = report.datasets[0]
    if change == "missing":
        first = first.model_copy(
            update={"artifact_hash": "f" * 64, "artifact_path": "f" * 64 + ".json"}
        )
    elif change == "corrupted":
        (tmp_path / first.artifact_path).write_bytes(b"{}")
    elif change == "wrong_count":
        first = first.model_copy(update={"record_count": 999})
    elif change == "wrong_ticker":
        first = first.model_copy(update={"ticker": "OTHER.L"})
    elif change == "duplicate":
        report = report.model_copy(update={"datasets": report.datasets + (first,)})
    report = report.model_copy(update={"datasets": (first, *report.datasets[1:])})
    raw, review = rights("eodhd")
    with pytest.raises(ValueError, match="PROVIDER_(PROOF_INVALID|DATASET_NOT_QUALIFIED)"):
        qualify_probe(report, review, raw, artifacts, clock=lambda: NOW)


def test_stale_probe_cannot_requalify_itself_using_its_own_timestamp(tmp_path):
    artifacts = QualificationArtifacts(tmp_path, secrets=("synthetic-api-secret",))
    report = probe_eodhd("synthetic-api-secret", (sample(),), NOW, artifacts, fetcher=Fetcher())
    raw, review = rights("eodhd")
    with pytest.raises(ValueError, match="PROVIDER_PROBE_STALE"):
        qualify_probe(report, review, raw, artifacts, clock=lambda: NOW + timedelta(days=1))


def test_proof_reader_rejects_symlinks_and_writer_matches_manifest_size_bound(tmp_path):
    artifacts = QualificationArtifacts(tmp_path, secrets=())
    digest, name = artifacts.save({"test": "test"})
    original = tmp_path / "original.json"
    (tmp_path / name).rename(original)
    (tmp_path / name).symlink_to(original)
    with pytest.raises(ValueError, match="PROOF_INVALID"):
        artifacts.read(digest, name)
    with pytest.raises(ValueError, match="SIZE_INVALID"):
        artifacts.save_bytes(b"x" * 2_000_001)
