"""Verified ISA universe only: this interface has no account or order capability."""

from datetime import datetime, timedelta
from typing import Protocol

from money.schemas.contracts import InstrumentMetadata, ResearchMandate


class Trading212EligibilityService(Protocol):
    def get_isa_universe(self) -> tuple[InstrumentMetadata, ...]: ...
    def is_available_in_isa(self, ticker: str) -> bool | None: ...
    def is_currently_available(self, ticker: str) -> bool | None: ...
    def get_instrument_type(self, ticker: str) -> str | None: ...
    def get_quote_currency(self, ticker: str) -> str | None: ...
    def get_instrument_metadata(self, ticker: str) -> InstrumentMetadata | None: ...


class Trading212EligibilityAdapter:
    """Consumes a verified ISA-specific export; a generic ticker listing is insufficient."""

    def __init__(self, instruments: tuple[InstrumentMetadata, ...]) -> None:
        if len({i.ticker for i in instruments}) != len(instruments):
            raise ValueError("duplicate eligibility instrument")
        self._instruments = {i.ticker: i for i in instruments}

    def get_isa_universe(self) -> tuple[InstrumentMetadata, ...]:
        return tuple(i for i in self._instruments.values() if i.isa_available is True)

    def get_instrument_metadata(self, ticker: str) -> InstrumentMetadata | None:
        return self._instruments.get(ticker)

    def is_available_in_isa(self, ticker: str) -> bool | None:
        item = self.get_instrument_metadata(ticker)
        return item.isa_available if item else None

    def is_currently_available(self, ticker: str) -> bool | None:
        item = self.get_instrument_metadata(ticker)
        return item.currently_available if item else None

    def get_instrument_type(self, ticker: str) -> str | None:
        item = self.get_instrument_metadata(ticker)
        return item.instrument_type if item else None

    def get_quote_currency(self, ticker: str) -> str | None:
        item = self.get_instrument_metadata(ticker)
        return item.quote_currency if item else None


def eligibility_failures(
    instrument: InstrumentMetadata | None,
    mandate: ResearchMandate,
    now: datetime,
) -> tuple[str, ...]:
    if instrument is None:
        return ("ISA_ELIGIBILITY_UNKNOWN",)
    failures: list[str] = []
    if instrument.isa_available is not True:
        failures.append("ISA_ELIGIBILITY_UNCONFIRMED")
    if instrument.currently_available is not True:
        failures.append("INSTRUMENT_UNAVAILABLE")
    if instrument.instrument_type not in mandate.instrument_types:
        failures.append("INSTRUMENT_TYPE_EXCLUDED")
    if instrument.quote_currency not in mandate.quote_currencies:
        failures.append("CURRENCY_EXCLUDED")
    if not instrument.activities_verified or not instrument.business_activities:
        failures.append("ETHICAL_SCREEN_UNKNOWN")
    if set(instrument.business_activities) & set(mandate.excluded_activities):
        failures.append("PROHIBITED_ACTIVITY")
    if not now - timedelta(hours=24) <= instrument.verified_at <= now:
        failures.append("ELIGIBILITY_STALE")
    return tuple(failures)
