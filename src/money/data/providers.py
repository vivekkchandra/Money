"""Provider-neutral coverage declarations; presence of a ticker never implies a dataset."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal, Protocol

from pydantic import AwareDatetime, Field

from money.schemas.contracts import Contract, EvidenceRecord, InstrumentMetadata

Dataset = Literal[
    "ohlcv", "financial", "filing", "announcement", "news", "corporate_action", "macro"
]


class ProviderCoverage(Contract):
    provider: str = Field(min_length=1)
    datasets: tuple[Dataset, ...]
    currencies: tuple[str, ...]
    instrument_types: tuple[str, ...]
    earliest_observation: AwareDatetime
    latest_observation: AwareDatetime
    historical_publication_times: bool
    production_qualified: bool = False
    development_only: bool = False

    def supports(
        self,
        instrument: InstrumentMetadata,
        dataset: Dataset,
        as_of: datetime,
        *,
        historical: bool,
        production: bool,
    ) -> bool:
        return bool(
            dataset in self.datasets
            and instrument.quote_currency in self.currencies
            and instrument.instrument_type in self.instrument_types
            and self.earliest_observation <= as_of <= self.latest_observation
            and (not historical or self.historical_publication_times)
            and (
                not production
                or (
                    self.production_qualified
                    and not self.development_only
                    and self.provider.casefold() != "yfinance"
                )
            )
        )


class MarketDataAdapter(Protocol):
    coverage: ProviderCoverage

    def fetch(
        self,
        instrument: InstrumentMetadata,
        dataset: Dataset,
        as_of: datetime,
        snapshot_id: str,
    ) -> tuple[EvidenceRecord, ...]: ...


def select_providers(
    providers: tuple[MarketDataAdapter, ...],
    instrument: InstrumentMetadata,
    dataset: Dataset,
    as_of: datetime,
    *,
    historical: bool,
    production: bool,
) -> tuple[MarketDataAdapter, ...]:
    selected = tuple(
        provider
        for provider in providers
        if provider.coverage.supports(
            instrument,
            dataset,
            as_of,
            historical=historical,
            production=production,
        )
    )
    if not selected:
        raise ValueError(f"No qualified provider coverage for {dataset}")
    return selected


class UKXBRLAdapter:
    """A bounded parsing seam; a qualified stream-read-xbrl converter is injected offline.

    Filing availability is supplied separately and checked in the resulting
    evidence. This adapter never fetches arbitrary client URLs or executes code.
    The converter owns mapping native rows/units into FinancialFact records.
    """

    def __init__(
        self,
        converter: Callable[[bytes, str, str], tuple[EvidenceRecord, ...]],
        *,
        maximum_archive_bytes: int = 10_000_000,
    ) -> None:
        if maximum_archive_bytes <= 0:
            raise ValueError("archive limit must be positive")
        self._converter = converter
        self._maximum_archive_bytes = maximum_archive_bytes

    def parse(self, archive: bytes, source_id: str, snapshot_id: str) -> tuple[EvidenceRecord, ...]:
        if not archive or len(archive) > self._maximum_archive_bytes:
            raise ValueError("archive is empty or exceeds the parser resource limit")
        records = tuple(
            EvidenceRecord.model_validate_json(item.model_dump_json())
            for item in self._converter(archive, source_id, snapshot_id)
        )
        if any(
            item.snapshot_id != snapshot_id
            or item.source_id != source_id
            or item.payload.kind != "financial"
            for item in records
        ):
            raise ValueError("XBRL converter returned inconsistent financial provenance")
        return records
