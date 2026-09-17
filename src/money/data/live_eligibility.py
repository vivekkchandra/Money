"""Current broker metadata joined to independent, expiring ISA/ethical reviews.

Trading 212's documented metadata API has no ISA eligibility flag. Neither a
listing nor a successful metadata request establishes ISA purchase eligibility.
The review source is independent of the startup research manifest and may be
refreshed by an operator; unknown, stale, missing and conflicting rows fail closed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from pydantic import Field, model_validator

from money.adapters.eligibility import Trading212EligibilityAdapter, eligibility_failures
from money.data.identifiers import InstrumentIdentifiers
from money.data.uk.live import Trading212MetadataProvider
from money.schemas.contracts import Contract, InstrumentMetadata, ResearchMandate, utc_now


class EligibilityReview(Contract):
    metadata: InstrumentMetadata
    identifiers: InstrumentIdentifiers
    eligibility_proof_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    ethical_proof_hash: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def matching_identity(self) -> EligibilityReview:
        if (
            self.metadata.ticker != self.identifiers.ticker
            or self.metadata.quote_currency != self.identifiers.quote_currency
            or self.metadata.provider == "money-demo"
        ):
            raise ValueError("LIVE_ELIGIBILITY_IDENTITY_MISMATCH")
        return self


class Trading212LiveEligibilityService:
    """Refresh broker membership without inferring ISA/ethical verification.

    The callback must supply independently verified, hash-checked reviews from
    the administrator's catalogue. It is re-read on each access, so revocations
    take effect even while broker metadata is cached. Cache age never extends
    review freshness, and a failed broker refresh never serves stale membership.
    """

    def __init__(
        self,
        provider: Trading212MetadataProvider,
        reviews: Callable[[], tuple[EligibilityReview, ...]],
        *,
        clock: Callable[[], datetime] = utc_now,
        refresh_interval: timedelta = timedelta(minutes=10),
    ) -> None:
        if not timedelta(seconds=50) <= refresh_interval <= timedelta(minutes=10):
            raise ValueError("LIVE_ELIGIBILITY_REFRESH_INTERVAL_INVALID")
        self._provider = provider
        self._reviews = reviews
        self._clock = clock
        self._refresh_interval = refresh_interval
        self._fetched_at: datetime | None = None
        self._metadata: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _adapter(self) -> Trading212EligibilityAdapter:
        with self._lock:
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("ELIGIBILITY_TIMESTAMP_INVALID")
            if self._fetched_at is None or not (
                self._fetched_at <= now < self._fetched_at + self._refresh_interval
            ):
                # Clear before requesting: failure must not retain confirmed data.
                self._metadata, self._fetched_at = {}, None
                rows = self._provider.instruments()
                index: dict[str, dict[str, Any]] = {}
                for row in rows:
                    ticker = row.get("ticker")
                    if not isinstance(ticker, str) or not ticker or ticker in index:
                        raise ValueError("LIVE_ELIGIBILITY_METADATA_AMBIGUOUS")
                    index[ticker] = row
                self._metadata, self._fetched_at = index, now
            reviews = self._reviews()
            # I/O and lock contention may cross an identifier/review expiry.
            # Never evaluate current eligibility using a pre-retrieval clock.
            now = self._clock()
            if now.tzinfo is None or now.utcoffset() is None:
                raise ValueError("ELIGIBILITY_TIMESTAMP_INVALID")
            if not self._fetched_at <= now < self._fetched_at + self._refresh_interval:
                self._metadata, self._fetched_at = {}, None
                raise ValueError("LIVE_ELIGIBILITY_METADATA_STALE")
            # Revalidate even callbacks: no ambiguity can silently win a join.
            tickers = [review.metadata.ticker for review in reviews]
            broker_ids = [review.identifiers.trading212_id for review in reviews]
            if len(tickers) != len(set(tickers)) or len(broker_ids) != len(set(broker_ids)):
                raise ValueError("LIVE_ELIGIBILITY_DUPLICATE_IDENTITY")
            verified = []
            for review in reviews:
                identifiers, metadata = review.identifiers, review.metadata
                try:
                    identifiers.require_current(now)
                except ValueError:
                    continue
                broker = self._metadata.get(identifiers.trading212_id)
                if (
                    broker is None
                    or broker.get("isin") != identifiers.isin
                    or broker.get("currencyCode") != metadata.quote_currency
                    or broker.get("type") != metadata.instrument_type
                    or eligibility_failures(metadata, ResearchMandate(), now)
                ):
                    continue
                verified.append(metadata)
            return Trading212EligibilityAdapter(tuple(verified), clock=self._clock)

    def get_isa_universe(self) -> tuple[InstrumentMetadata, ...]:
        return self._adapter().get_isa_universe()

    def get_instrument_metadata(self, ticker: str) -> InstrumentMetadata | None:
        return self._adapter().get_instrument_metadata(ticker)

    def is_available_in_isa(self, ticker: str) -> bool | None:
        return self._adapter().is_available_in_isa(ticker)

    def is_currently_available(self, ticker: str) -> bool | None:
        return self._adapter().is_currently_available(ticker)

    def get_instrument_type(self, ticker: str) -> str | None:
        return self._adapter().get_instrument_type(ticker)

    def get_quote_currency(self, ticker: str) -> str | None:
        return self._adapter().get_quote_currency(ticker)
