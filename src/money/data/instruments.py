"""Bounded search over reviewed release evidence, never a guessed broker universe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from pydantic import AwareDatetime, Field, field_validator

from money.adapters.eligibility import eligibility_failures
from money.data.identifiers import InstrumentIdentifiers
from money.data.qualification import ProviderQualification
from money.schemas.contracts import Contract, InstrumentMetadata, ResearchMandate

if TYPE_CHECKING:
    from money.research.live import LiveManifest


class InstrumentSearchQuery(Contract):
    query: str = Field(min_length=1, max_length=80, pattern=r"^[^\x00-\x1f\x7f]+$")
    limit: int = Field(default=10, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=1000)

    @field_validator("query")
    @classmethod
    def nonempty_query(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A company name or identifier is required")
        return value.strip()


class InstrumentSearchResult(Contract):
    instrument_id: str
    ticker: str
    company: str
    exchange: str | None
    currency: str
    eligibility: Literal["VERIFIED_ELIGIBLE", "VERIFIED_INELIGIBLE", "UNKNOWN"]
    research_allowed: bool
    verified_at: AwareDatetime
    synthetic: bool = False
    canonical_symbol: str | None = None
    provider_symbol: str | None = None
    country: str | None = None
    instrument_type: str | None = None


class InstrumentSearchPage(Contract):
    instruments: tuple[InstrumentSearchResult, ...]
    total: int
    limit: int
    offset: int
    coverage: Literal["reviewed_catalogue", "public_provider"] = "reviewed_catalogue"
    mode: Literal["live", "demo", "live_rnd"]


@dataclass(frozen=True)
class ReviewedInstrument:
    metadata: InstrumentMetadata
    identifiers: InstrumentIdentifiers | None
    eligibility_proof_hash: str | None = None
    ethical_proof_hash: str | None = None

    def failures(self, mandate: ResearchMandate, now: datetime) -> tuple[str, ...]:
        failures = eligibility_failures(self.metadata, mandate, now)
        identifiers = self.identifiers
        if identifiers is not None:
            if (
                identifiers.ticker != self.metadata.ticker
                or identifiers.quote_currency != self.metadata.quote_currency
            ):
                failures += ("IDENTIFIER_MAPPING_MISMATCH",)
            try:
                identifiers.require_current(now)
            except ValueError:
                failures += ("IDENTIFIER_MAPPING_STALE",)
        return failures

    def result(self, now: datetime, *, synthetic: bool) -> InstrumentSearchResult:
        failures = self.failures(ResearchMandate(), now)
        fresh_identity = not set(failures).intersection(
            {
                "ELIGIBILITY_STALE",
                "IDENTIFIER_MAPPING_STALE",
                "IDENTIFIER_MAPPING_MISMATCH",
                "ELIGIBILITY_PROVENANCE_MISSING",
            }
        )
        metadata = self.metadata
        eligibility: Literal["VERIFIED_ELIGIBLE", "VERIFIED_INELIGIBLE", "UNKNOWN"] = "UNKNOWN"
        if fresh_identity:
            if metadata.isa_available is False or metadata.currently_available is False:
                eligibility = "VERIFIED_INELIGIBLE"
            elif metadata.isa_available is True and metadata.currently_available is True:
                eligibility = "VERIFIED_ELIGIBLE"
        return InstrumentSearchResult(
            instrument_id=metadata.ticker,
            ticker=metadata.ticker,
            company=metadata.company,
            exchange=self.identifiers.exchange if self.identifiers else None,
            currency=metadata.quote_currency,
            eligibility=eligibility,
            research_allowed=not failures,
            verified_at=metadata.verified_at,
            synthetic=synthetic,
        )

    def matches(self, query: str) -> bool:
        fields = [self.metadata.ticker, self.metadata.company]
        if self.identifiers:
            fields.extend(
                (
                    self.identifiers.company_name,
                    self.identifiers.exchange_ticker,
                    self.identifiers.isin,
                    self.identifiers.trading212_id,
                )
            )
        return any(query.casefold() in value.casefold() for value in fields)


@dataclass(frozen=True)
class InstrumentCatalogue:
    """Immutable startup catalogue; freshness is evaluated again on every use."""

    entries: tuple[ReviewedInstrument, ...]
    mode: Literal["live", "demo"]
    qualifications: tuple[ProviderQualification, ...] = ()

    def __post_init__(self) -> None:
        keys = [entry.metadata.ticker for entry in self.entries]
        broker_ids = [
            entry.identifiers.trading212_id for entry in self.entries if entry.identifiers
        ]
        exchange_ids = [
            (entry.identifiers.exchange, entry.identifiers.exchange_ticker)
            for entry in self.entries
            if entry.identifiers
        ]
        if any(len(items) != len(set(items)) for items in (keys, broker_ids, exchange_ids)):
            raise ValueError("INSTRUMENT_CATALOGUE_AMBIGUOUS")
        if self.mode == "live" and any(
            entry.identifiers is None
            or entry.metadata.ticker == "DEMO.L"
            or entry.metadata.provider == "money-demo"
            for entry in self.entries
        ):
            raise ValueError("LIVE_INSTRUMENT_INVALID")

    @classmethod
    def from_manifest(cls, manifest: LiveManifest) -> InstrumentCatalogue:
        return cls(
            tuple(
                ReviewedInstrument(
                    item.metadata,
                    item.identifiers,
                    item.eligibility_proof_hash,
                    item.ethical_proof_hash,
                )
                for item in getattr(manifest, "reviewed_instruments", manifest.instruments)
            ),
            "live",
            manifest.provider_qualifications,
        )

    @classmethod
    def demonstration(cls, now: datetime) -> InstrumentCatalogue:
        """Only the trusted API environment may enable this explicitly synthetic entry."""
        metadata = InstrumentMetadata(
            ticker="DEMO.L",
            company="Money synthetic demonstration",
            instrument_type="STOCK",
            quote_currency="GBX",
            isa_available=True,
            currently_available=True,
            business_activities=("synthetic demonstration",),
            activities_verified=True,
            verified_at=now,
            source="Money synthetic demonstration",
            provider="money-demo",
            source_id="demo:instrument:v1",
        )
        return cls((ReviewedInstrument(metadata, None),), "demo")

    def require_current(self, now: datetime) -> None:
        for qualification in self.qualifications:
            if not qualification.datasets:
                raise ValueError("PROVIDER_COVERAGE_MISSING")
            for dataset in qualification.datasets:
                qualification.require(dataset, now)

    def search(self, query: InstrumentSearchQuery, now: datetime) -> InstrumentSearchPage:
        self.require_current(now)
        entries = sorted(
            (entry for entry in self.entries if entry.matches(query.query)),
            key=lambda entry: (entry.metadata.ticker != query.query.upper(), entry.metadata.ticker),
        )
        return InstrumentSearchPage(
            instruments=tuple(
                entry.result(now, synthetic=self.mode == "demo")
                for entry in entries[query.offset : query.offset + query.limit]
            ),
            total=len(entries),
            limit=query.limit,
            offset=query.offset,
            mode=self.mode,
        )

    def admission_failures(
        self, ticker: str, mandate: ResearchMandate, now: datetime
    ) -> tuple[str, ...]:
        self.require_current(now)
        entry = next((item for item in self.entries if item.metadata.ticker == ticker), None)
        return entry.failures(mandate, now) if entry else ("ISA_ELIGIBILITY_UNKNOWN",)
