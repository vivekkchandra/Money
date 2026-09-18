"""Verified live GBX stock universe; no account or order capability."""

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from money.schemas.contracts import (
    EXCLUDED_ACTIVITIES,
    InstrumentMetadata,
    ResearchMandate,
    utc_now,
)

ELIGIBILITY_MAXIMUM_AGE = timedelta(hours=24)
_UNKNOWN_ACTIVITIES = frozenset(
    {
        "unknown",
        "unclassified",
        "unverified",
        "not_available",
        "not_classified",
        "unknown_material_exposure",
        "unknown_material_business_exposure",
    }
)


def _activity(value: str) -> str:
    """Normalize structured labels, not free-text/LLM guesses about a business."""
    return re.sub(r"[\s-]+", "_", value.strip().casefold())


def _current(instrument: InstrumentMetadata, now: datetime) -> bool:
    return (
        now.tzinfo is not None
        and now.utcoffset() is not None
        and instrument.verified_at.tzinfo is not None
        and instrument.verified_at.utcoffset() is not None
        and now - ELIGIBILITY_MAXIMUM_AGE < instrument.verified_at <= now
    )


def _provenance_valid(instrument: InstrumentMetadata) -> bool:
    return all(
        value.strip() for value in (instrument.source, instrument.provider, instrument.source_id)
    )


class Trading212EligibilityService(Protocol):
    def get_universe(self) -> tuple[InstrumentMetadata, ...]: ...
    def is_currently_available(self, ticker: str) -> bool | None: ...
    def get_instrument_type(self, ticker: str) -> str | None: ...
    def get_quote_currency(self, ticker: str) -> str | None: ...
    def get_instrument_metadata(self, ticker: str) -> InstrumentMetadata | None: ...


class Trading212EligibilityAdapter:
    """Consumes current broker membership plus independently qualified ethics.

    A listing alone does not establish ethical/provider research qualification.
    Account type and purchase availability are deliberately not asserted.
    """

    def __init__(
        self,
        instruments: tuple[InstrumentMetadata, ...],
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if len({i.ticker for i in instruments}) != len(instruments):
            raise ValueError("duplicate eligibility instrument")
        self._instruments = {i.ticker: i for i in instruments}
        self._clock = clock

    def get_universe(self) -> tuple[InstrumentMetadata, ...]:
        now = self._clock()
        return tuple(
            item
            for _, item in sorted(self._instruments.items())
            if not eligibility_failures(item, ResearchMandate(), now)
        )

    def get_isa_universe(self) -> tuple[InstrumentMetadata, ...]:
        """Deprecated compatibility alias; results make no ISA eligibility claim."""
        return self.get_universe()

    def get_instrument_metadata(self, ticker: str) -> InstrumentMetadata | None:
        return self._instruments.get(ticker)

    def is_available_in_isa(self, ticker: str) -> bool | None:
        """Deprecated compatibility method: current policy does not verify ISAs."""
        return None

    def is_currently_available(self, ticker: str) -> bool | None:
        """Return verified live membership, never permission to purchase."""
        item = self.get_instrument_metadata(ticker)
        return (
            item.currently_available
            if item and _provenance_valid(item) and _current(item, self._clock())
            else None
        )

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
        return ("INSTRUMENT_ELIGIBILITY_UNKNOWN",)
    failures: list[str] = []
    if instrument.currently_available is not True:
        failures.append("INSTRUMENT_UNAVAILABLE")
    if (
        instrument.instrument_type != "STOCK"
        or instrument.instrument_type not in mandate.instrument_types
    ):
        failures.append("INSTRUMENT_TYPE_EXCLUDED")
    if (
        instrument.quote_currency != "GBX"
        or instrument.quote_currency not in mandate.quote_currencies
    ):
        failures.append("CURRENCY_EXCLUDED")
    activities = {_activity(value) for value in instrument.business_activities}
    if (
        not instrument.activities_verified
        or not activities
        or any(not item or item in _UNKNOWN_ACTIVITIES for item in activities)
    ):
        failures.append("ETHICAL_SCREEN_UNKNOWN")
    exclusions = {
        _activity(value) for value in (*EXCLUDED_ACTIVITIES, *mandate.excluded_activities)
    }
    if activities & exclusions:
        failures.append("PROHIBITED_ACTIVITY")
    if not _provenance_valid(instrument):
        failures.append("ELIGIBILITY_PROVENANCE_MISSING")
    if not _current(instrument, now):
        failures.append("ELIGIBILITY_STALE")
    return tuple(failures)
