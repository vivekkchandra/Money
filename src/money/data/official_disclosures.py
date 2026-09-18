"""Hash-bound official issuer disclosures admitted by the existing source review.

Capture alone is not qualification. These proofs bind reviewed accounting units,
technical conversion, issuer identity and publication evidence to exact normalized
records. Source licensing is admitted globally through ProviderQualification.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, Field, model_validator

from money.data.identifiers import InstrumentIdentifiers
from money.data.qualification import ProviderQualification
from money.data.security import validate_url
from money.schemas.contracts import Contract, DocumentFact, EvidenceRecord, FinancialFact
from money.usage_policy import UsageMode


class OfficialDisclosureProof(Contract):
    """One exact issuer document, not an approval manufactured from captured bytes."""

    version: Literal["money-official-disclosure-v1"] = "money-official-disclosure-v1"
    ticker: str = Field(min_length=1, max_length=32)
    trading212_id: str = Field(min_length=1, max_length=100)
    isin: str = Field(pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    eodhd_symbol: str = Field(min_length=1, max_length=100)
    legal_name: str = Field(min_length=1, max_length=200)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    company_number: str | None = Field(default=None, pattern=r"^[A-Z0-9]{8}$")
    provider: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,49}$")
    source_url: str = Field(min_length=1, max_length=2000)
    source_id: str = Field(min_length=1, max_length=500)
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    identity_evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    conversion_evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    accounting_currency_evidence_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    accounting_currency: Literal["GBP"]
    publication_times: Literal["AS_RETRIEVED", "ORIGINAL_PUBLICATION_VERIFIED"]
    publication_evidence_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    publication_time: AwareDatetime
    retrieval_time: AwareDatetime
    fresh_until: AwareDatetime
    evidence_hashes: tuple[str, ...] = Field(min_length=2, max_length=1000)

    @property
    def artifact_hashes(self) -> frozenset[str]:
        """Actual bytes which a qualification bundle must retain and verify."""
        return frozenset(
            value for value in (
                self.document_sha256, self.identity_evidence_hash,
                self.conversion_evidence_hash, self.accounting_currency_evidence_hash,
                self.publication_evidence_hash,
            ) if value is not None
        )

    @model_validator(mode="after")
    def source_integrity(self) -> OfficialDisclosureProof:
        import re

        parsed = urlsplit(self.source_url)
        host = parsed.hostname
        if host is None or parsed.query or parsed.fragment:
            raise ValueError("OFFICIAL_DISCLOSURE_URL_INVALID")
        validate_url(self.source_url, frozenset({host}))
        if self.provider in {
            "companies-house", "trading212", "eodhd", "money-demo", "yfinance",
        }:
            raise ValueError("OFFICIAL_DISCLOSURE_PROVIDER_INVALID")
        if (
            len(set(self.evidence_hashes)) != len(self.evidence_hashes)
            or any(re.fullmatch(r"[a-f0-9]{64}", item) is None for item in self.evidence_hashes)
        ):
            raise ValueError("OFFICIAL_DISCLOSURE_RECORD_HASH_INVALID")
        if (
            self.publication_time > self.retrieval_time
            or self.fresh_until <= self.retrieval_time
            or (
                self.publication_times == "AS_RETRIEVED"
                and self.publication_time != self.retrieval_time
            )
            or (
                self.publication_times == "ORIGINAL_PUBLICATION_VERIFIED"
                and self.publication_evidence_hash is None
            )
        ):
            raise ValueError("OFFICIAL_DISCLOSURE_PUBLICATION_INVALID")
        return self


def validate_disclosure_records(
    proof: OfficialDisclosureProof,
    identifiers: InstrumentIdentifiers,
    records: tuple[EvidenceRecord, ...],
    *, issuer_company_number: str | None = None,
) -> tuple[EvidenceRecord, ...]:
    """Bind normalized facts to one exact identity and technical source review."""
    if (
        proof.ticker != identifiers.ticker
        or proof.trading212_id != identifiers.trading212_id
        or proof.isin != identifiers.isin
        or proof.eodhd_symbol != dict(identifiers.provider_symbols).get("eodhd")
        or " ".join(proof.legal_name.upper().split())
        != " ".join(identifiers.company_name.upper().split())
        or proof.company_number != (issuer_company_number or identifiers.companies_house_number)
        or (
            issuer_company_number is not None
            and identifiers.companies_house_number is not None
            and issuer_company_number != identifiers.companies_house_number
        )
    ):
        raise ValueError("OFFICIAL_DISCLOSURE_IDENTITY_MISMATCH")
    index = {record.hash: record for record in records}
    if not set(proof.evidence_hashes) <= index.keys():
        raise ValueError("OFFICIAL_DISCLOSURE_RECORD_MISSING")
    selected = tuple(index[digest] for digest in proof.evidence_hashes)
    has_filing = False
    has_financial = False
    for record in selected:
        if (
            record.provider != proof.provider
            or record.source_id != proof.source_id
            or record.canonical_source_id != f"{proof.provider}:{proof.source_id}"
            or record.publication_time != proof.publication_time
            or record.retrieval_time != proof.retrieval_time
            or record.fresh_until > proof.fresh_until
            or record.conflicting
            or not record.pit_safe
            or not record.available_at(proof.retrieval_time)
        ):
            raise ValueError("OFFICIAL_DISCLOSURE_RECORD_PROVENANCE_MISMATCH")
        if isinstance(record.payload, DocumentFact) and record.payload.kind == "filing":
            if record.payload.url != proof.source_url:
                raise ValueError("OFFICIAL_DISCLOSURE_FILING_URL_MISMATCH")
            has_filing = True
        elif isinstance(record.payload, FinancialFact) and record.payload.metric != "spread_bps":
            if (
                record.payload.unit != proof.accounting_currency
                or record.payload.period_end > proof.publication_time
            ):
                raise ValueError("OFFICIAL_DISCLOSURE_FINANCIAL_UNITS_OR_PIT_INVALID")
            has_financial = True
        else:
            raise ValueError("OFFICIAL_DISCLOSURE_DATASET_INVALID")
    if not has_filing or not has_financial:
        raise ValueError("OFFICIAL_DISCLOSURE_FINANCIAL_AND_FILING_REQUIRED")
    return selected


def require_official_disclosures(
    proofs: tuple[OfficialDisclosureProof, ...],
    identifiers: InstrumentIdentifiers,
    records: tuple[EvidenceRecord, ...],
    qualifications: dict[str, ProviderQualification],
    now: datetime,
    *,
    jurisdiction: str | None,
    jurisdiction_proof_hash: str | None,
    issuer_company_number: str | None = None,
    usage_mode: UsageMode = UsageMode.HOSTED_COMMERCIAL_PRODUCTION,
) -> None:
    """Require current, technically admitted filing and accounting observations."""
    identifiers.require_current(now)
    if not proofs or not jurisdiction or not jurisdiction_proof_hash:
        raise ValueError("OFFICIAL_DISCLOSURE_EVIDENCE_REQUIRED")
    hashes: set[str] = set()
    documents: set[str] = set()
    for proof in proofs:
        if (
            proof.jurisdiction != jurisdiction
            or proof.identity_evidence_hash != jurisdiction_proof_hash
            or proof.document_sha256 in documents
            or hashes.intersection(proof.evidence_hashes)
        ):
            raise ValueError("OFFICIAL_DISCLOSURE_IDENTITY_OR_DUPLICATE_INVALID")
        qualification = qualifications.get(proof.provider)
        if qualification is None:
            raise ValueError("OFFICIAL_DISCLOSURE_PROVIDER_UNQUALIFIED")
        if (
            qualification.publication_times != proof.publication_times
            or "STOCK" not in qualification.instrument_types
            or identifiers.quote_currency not in qualification.currencies
            or proof.jurisdiction not in qualification.geography
            or not proof.retrieval_time <= now < proof.fresh_until
        ):
            raise ValueError("OFFICIAL_DISCLOSURE_COVERAGE_OR_FRESHNESS_INVALID")
        for record in validate_disclosure_records(
            proof, identifiers, records, issuer_company_number=issuer_company_number,
        ):
            qualification.require(record.payload.kind, now, usage_mode=usage_mode)
            if (
                record.fresh_until <= now
                or record.retrieval_time > now
                or record.observation_time < qualification.earliest_observation
                or not record.available_at(now)
            ):
                raise ValueError("OFFICIAL_DISCLOSURE_EVIDENCE_STALE_OR_PIT_INVALID")
        hashes.update(proof.evidence_hashes)
        documents.add(proof.document_sha256)
    required = {
        record.hash for record in records
        if record.payload.kind == "filing"
        or (isinstance(record.payload, FinancialFact) and record.payload.metric != "spread_bps")
    }
    if not required <= hashes:
        raise ValueError("OFFICIAL_DISCLOSURE_UNBOUND_FINANCIAL_OR_FILING")
