"""Live read-only admission probes, distinct from already-qualified research.

No probe grants redistribution rights, original-publication history, or financial
document coverage merely because credentials work. Production qualification also
requires an explicit administrator review of usage rights and the observed scope.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import AwareDatetime, Field, TypeAdapter

from money.data.identifiers import InstrumentIdentifiers
from money.data.qualification import ProviderQualification
from money.data.security import ProviderFailure, SafeFetcher, SourceSecurityError
from money.data.uk.live import CompaniesHouseProvider, EODHDProvider
from money.schemas.contracts import Contract, EvidenceRecord, utc_now


class ProviderAdmissionReview(Contract):
    """Separate, hash-addressed operator judgment; never inferred from API access."""

    provider: Literal["eodhd", "companies-house"]
    reviewed_by: str = Field(min_length=1, max_length=200)
    usage_purpose: str = Field(min_length=1, max_length=500)
    storage_policy: str = Field(min_length=1, max_length=500)
    redistribution: Literal["PROHIBITED", "FACTS_AND_LINKS", "LICENSED"]
    attribution: str = Field(min_length=1, max_length=500)
    source_documentation: str = Field(min_length=1, max_length=2000)
    rights_evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_at: AwareDatetime
    valid_until: AwareDatetime
    ethical_research_datasets: tuple[str, ...] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class DatasetProbe(Contract):
    dataset: str
    ticker: str
    status: Literal["RETRIEVED", "EMPTY", "FAILED"]
    record_count: int = Field(ge=0)
    error_code: str | None = None
    artifact_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    artifact_path: str | None = None
    earliest_observation: AwareDatetime | None = None
    latest_observation: AwareDatetime | None = None


class ProviderProbeReport(Contract):
    version: Literal["money-provider-probe-v1"] = "money-provider-probe-v1"
    provider: Literal["eodhd", "companies-house"]
    retrieved_at: AwareDatetime
    samples: tuple[InstrumentIdentifiers, ...]
    datasets: tuple[DatasetProbe, ...]
    rights_review_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    rights_evidence_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    publication_times: Literal["AS_RETRIEVED"] = "AS_RETRIEVED"
    historical_publication_verified: Literal[False] = False
    financial_documents_verified: Literal[False] = False


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


class QualificationArtifacts:
    """Write only new content-addressed files after checking configured secrets."""

    def __init__(self, root: Path, *, secrets: tuple[str, ...]) -> None:
        self.root = root
        self._secrets = tuple(value.encode() for value in secrets if value)

    def save(self, value: Any) -> tuple[str, str]:
        return self.save_bytes(json_bytes(value), extension="json")

    def save_bytes(
        self, raw: bytes, *, extension: Literal["json", "bin"] = "bin"
    ) -> tuple[str, str]:
        if not raw or len(raw) > 2_000_000:
            raise ValueError("QUALIFICATION_ARTIFACT_SIZE_INVALID")
        # No accidental provider-echoed key may reach persistent artifacts.
        if any(secret in raw for secret in self._secrets):
            raise ValueError("QUALIFICATION_ARTIFACT_SECRET_DETECTED")
        digest = hashlib.sha256(raw).hexdigest()
        name = digest + "." + extension
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        try:
            with path.open("xb") as stream:
                stream.write(raw)
        except FileExistsError:
            if self.read(digest, name) != raw:
                raise ValueError("QUALIFICATION_ARTIFACT_COLLISION") from None
        return digest, name

    def read(self, digest: str | None, name: str | None) -> bytes:
        """Verify a bounded content-addressed artifact without following symlinks."""
        if (
            digest is None
            or re.fullmatch(r"[a-f0-9]{64}", digest) is None
            or name not in {digest + ".json", digest + ".bin"}
        ):
            raise ValueError("PROVIDER_PROOF_INVALID")
        try:
            descriptor = os.open(self.root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                metadata = os.fstat(stream.fileno())
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 2_000_000:
                    raise ValueError("PROVIDER_PROOF_INVALID")
                raw = stream.read(2_000_001)
            if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError("PROVIDER_PROOF_INVALID")
            return raw
        except OSError as error:
            raise ValueError("PROVIDER_PROOF_INVALID") from error


def _failure_code(error: Exception) -> str:
    # Provider responses/URLs and Pydantic diagnostics can carry secrets.
    if isinstance(error, ProviderFailure):
        return (
            error.code
            if error.code in {"PROVIDER_UNAVAILABLE", "PROVIDER_TIMEOUT"}
            else "PROVIDER_FAILED"
        )
    if isinstance(error, SourceSecurityError):
        return "SOURCE_SECURITY_CHECK_FAILED"
    return "PROVIDER_RESPONSE_VALIDATION_FAILED"


def _record_probe(
    provider: str,
    dataset: str,
    ticker: str,
    records: tuple[EvidenceRecord, ...],
    artifacts: QualificationArtifacts,
) -> DatasetProbe:
    digest, path = artifacts.save(
        {
            "provider": provider,
            "ticker": ticker,
            "dataset": dataset,
            "records": [record.model_dump(mode="json") for record in records],
        }
    )
    return DatasetProbe(
        dataset=dataset,
        ticker=ticker,
        status="RETRIEVED" if records else "EMPTY",
        record_count=len(records),
        artifact_hash=digest,
        artifact_path=path,
        earliest_observation=min((record.observation_time for record in records), default=None),
        latest_observation=max((record.observation_time for record in records), default=None),
    )


def probe_eodhd(
    api_key: str,
    samples: tuple[InstrumentIdentifiers, ...],
    now: datetime,
    artifacts: QualificationArtifacts,
    *,
    fetcher: SafeFetcher | None = None,
) -> ProviderProbeReport:
    if not samples:
        raise ValueError("QUALIFICATION_SAMPLES_REQUIRED")
    # This is an explicitly UNQUALIFIED normalization scope, not admission proof.
    scope = ProviderQualification(
        provider="eodhd",
        datasets=("ohlcv", "corporate_action", "news"),
        earliest_observation=now - timedelta(days=365),
        publication_times="AS_RETRIEVED",
        maximum_age_seconds=86400,
        production_qualified=False,
        verified_at=now,
        valid_until=now + timedelta(days=1),
        attribution="EODHD",
        source_documentation="https://eodhd.com/financial-apis",
    )
    provider = EODHDProvider(api_key, scope, fetcher=fetcher)
    results = []
    for identifiers in samples:
        for dataset in scope.datasets:
            try:
                records = provider._fetch_records(
                    identifiers, dataset, "provider-admission-probe", now
                )
                results.append(
                    _record_probe("eodhd", dataset, identifiers.ticker, records, artifacts)
                )
            except Exception as error:
                results.append(
                    DatasetProbe(
                        dataset=dataset,
                        ticker=identifiers.ticker,
                        status="FAILED",
                        record_count=0,
                        error_code=_failure_code(error),
                    )
                )
    return ProviderProbeReport(
        provider="eodhd", retrieved_at=now, samples=samples, datasets=tuple(results)
    )


def probe_companies_house(
    api_key: str,
    samples: tuple[InstrumentIdentifiers, ...],
    now: datetime,
    artifacts: QualificationArtifacts,
    *,
    fetcher: SafeFetcher | None = None,
) -> ProviderProbeReport:
    if not samples:
        raise ValueError("QUALIFICATION_SAMPLES_REQUIRED")
    provider = CompaniesHouseProvider(api_key, fetcher=fetcher)
    results = []
    for identifiers in samples:
        try:
            profile = provider.company(identifiers, now)
            profile_hash, profile_path = artifacts.save(profile)
            results.append(
                DatasetProbe(
                    dataset="company",
                    ticker=identifiers.ticker,
                    status="RETRIEVED",
                    record_count=1,
                    artifact_hash=profile_hash,
                    artifact_path=profile_path,
                )
            )
            records = provider.filings(identifiers, "provider-admission-probe", now)
            results.append(
                _record_probe("companies-house", "filing", identifiers.ticker, records, artifacts)
            )
        except Exception as error:
            results.append(
                DatasetProbe(
                    dataset="filing",
                    ticker=identifiers.ticker,
                    status="FAILED",
                    record_count=0,
                    error_code=_failure_code(error),
                )
            )
    return ProviderProbeReport(
        provider="companies-house", retrieved_at=now, samples=samples, datasets=tuple(results)
    )


def _verify_observation(
    report: ProviderProbeReport,
    probe: DatasetProbe,
    artifacts: QualificationArtifacts,
) -> None:
    raw = artifacts.read(probe.artifact_hash, probe.artifact_path)
    try:
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError("PROVIDER_PROOF_INVALID")
        sample = next(item for item in report.samples if item.ticker == probe.ticker)
        if probe.dataset == "company":
            if (
                value.get("company_number") != sample.companies_house_number
                or not isinstance(value.get("company_name"), str)
                or not value["company_name"].strip()
                or probe.record_count != 1
                or probe.status != "RETRIEVED"
            ):
                raise ValueError("PROVIDER_PROOF_INVALID")
            return
        if (
            value.get("provider") != report.provider
            or value.get("ticker") != probe.ticker
            or value.get("dataset") != probe.dataset
        ):
            raise ValueError("PROVIDER_PROOF_INVALID")
        records = TypeAdapter(tuple[EvidenceRecord, ...]).validate_python(value.get("records"))
        if (
            len(records) != probe.record_count
            or probe.status != ("RETRIEVED" if records else "EMPTY")
            or probe.earliest_observation
            != min((item.observation_time for item in records), default=None)
            or probe.latest_observation
            != max((item.observation_time for item in records), default=None)
            or any(
                item.provider != report.provider
                or item.payload.kind != probe.dataset
                or item.snapshot_id != "provider-admission-probe"
                or item.retrieval_time != report.retrieved_at
                or item.publication_time != report.retrieved_at
                or not item.available_at(report.retrieved_at)
                for item in records
            )
        ):
            raise ValueError("PROVIDER_PROOF_INVALID")
    except (ValueError, StopIteration, TypeError) as error:
        raise ValueError("PROVIDER_PROOF_INVALID") from error


def qualify_probe(
    report: ProviderProbeReport,
    review: ProviderAdmissionReview,
    rights_evidence: bytes,
    artifacts: QualificationArtifacts,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> ProviderQualification:
    """Admit only observed datasets/currencies after fresh explicit rights review.

    All configured samples must succeed. An empty event feed proves connectivity,
    but at least one actual event is required to qualify its normalization. These
    probes cannot qualify original-publication history or financial documents.
    """
    now = clock()
    if (
        now.tzinfo is None
        or now.utcoffset() is None
        or not report.retrieved_at <= now < report.retrieved_at + timedelta(days=1)
    ):
        raise ValueError("PROVIDER_PROBE_STALE")
    if (
        review.provider != report.provider
        or not review.reviewed_at <= now < review.valid_until
        or hashlib.sha256(rights_evidence).hexdigest() != review.rights_evidence_hash
    ):
        raise ValueError("PROVIDER_ADMISSION_REVIEW_INVALID")
    required = ("ohlcv", "corporate_action", "news") if report.provider == "eodhd" else ("filing",)
    sampled_tickers = {item.ticker for item in report.samples}
    if not sampled_tickers or len(sampled_tickers) != len(report.samples):
        raise ValueError("QUALIFICATION_SAMPLES_INVALID")
    for sample in report.samples:
        sample.require_current(now)
    observed_datasets = required if report.provider == "eodhd" else ("company", "filing")
    if {item.dataset for item in report.datasets} != set(observed_datasets):
        raise ValueError("PROVIDER_DATASET_NOT_QUALIFIED")
    for dataset in observed_datasets:
        observations = [probe for probe in report.datasets if probe.dataset == dataset]
        if (
            len(observations) != len(report.samples)
            or {probe.ticker for probe in observations} != sampled_tickers
            or any(probe.status == "FAILED" for probe in observations)
            or not any(probe.status == "RETRIEVED" and probe.record_count for probe in observations)
        ):
            raise ValueError("PROVIDER_DATASET_NOT_QUALIFIED")
        for observation in observations:
            _verify_observation(report, observation, artifacts)
    artifacts.save_bytes(rights_evidence)
    # Persist review text as an actual artifact; never merely invent its digest.
    review_hash, _ = artifacts.save(review.model_dump(mode="json"))
    reviewed_report = report.model_copy(
        update={
            "rights_review_hash": review_hash,
            "rights_evidence_hash": review.rights_evidence_hash,
        }
    )
    report_hash, _ = artifacts.save(reviewed_report.model_dump(mode="json"))
    earliest = min(
        probe.earliest_observation
        for probe in report.datasets
        if probe.earliest_observation is not None
        and probe.dataset == ("ohlcv" if report.provider == "eodhd" else "filing")
    )
    return ProviderQualification(
        provider=report.provider,
        datasets=required,
        currencies=tuple(sorted({sample.quote_currency for sample in report.samples})),
        earliest_observation=earliest,
        publication_times="AS_RETRIEVED",
        maximum_age_seconds=86400,
        production_qualified=True,
        qualified_by=review.reviewed_by,
        qualification_report_hash=report_hash,
        verified_at=report.retrieved_at,
        valid_until=min(report.retrieved_at + timedelta(days=1), review.valid_until),
        usage_purpose=review.usage_purpose,
        storage_policy=review.storage_policy,
        redistribution=review.redistribution,
        attribution=review.attribution,
        source_documentation=review.source_documentation,
    )
