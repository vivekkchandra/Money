"""Freeze every admitted member before bounded, evidence-only attention screening.

This is not a return forecast or an eligibility authority. Admission comes from
the existing eligibility contracts; a missing research-data bundle remains in
the frozen universe with an explicit failure and cannot enter expensive work.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal, Self

from pydantic import AwareDatetime, Field, model_validator

from money.adapters.eligibility import ELIGIBILITY_MAXIMUM_AGE, eligibility_failures
from money.data.live_eligibility import EligibilityReview
from money.data.normalization.prices import normalize_gbp
from money.data.security import ProviderFailure
from money.schemas.contracts import (
    Contract,
    InstrumentMetadata,
    PriceBar,
    ResearchMandate,
    ResearchSnapshot,
    content_hash,
    utc_now,
)


class FrozenUniverseMember(Contract):
    instrument: InstrumentMetadata
    qualification_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    valid_until: AwareDatetime
    snapshot_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evidence_status: str = Field(min_length=1, pattern=r"^[A-Z0-9_]+$")


class QualifiedUniverseSnapshot(Contract):
    version: Literal["money-qualified-universe-v1"] = "money-qualified-universe-v1"
    observed_at: AwareDatetime
    valid_until: AwareDatetime
    members: tuple[FrozenUniverseMember, ...]
    source_universe_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    hash: str = ""

    @model_validator(mode="after")
    def frozen_membership(self) -> Self:
        tickers = tuple(member.instrument.ticker for member in self.members)
        if tickers != tuple(sorted(set(tickers))):
            raise ValueError("UNIVERSE_MEMBERSHIP_NOT_CANONICAL")
        if self.valid_until <= self.observed_at:
            raise ValueError("UNIVERSE_MEMBERSHIP_EXPIRED")
        for member in self.members:
            if eligibility_failures(member.instrument, ResearchMandate(), self.observed_at):
                raise ValueError("UNIVERSE_MEMBER_NOT_QUALIFIED")
            if (
                not self.observed_at
                < member.valid_until
                <= (member.instrument.verified_at + ELIGIBILITY_MAXIMUM_AGE)
            ):
                raise ValueError("UNIVERSE_MEMBER_EXPIRED")
            if (member.snapshot_hash is not None) != (member.evidence_status == "READY"):
                raise ValueError("UNIVERSE_EVIDENCE_STATUS_INVALID")
        if self.members and self.valid_until != min(member.valid_until for member in self.members):
            raise ValueError("UNIVERSE_VALIDITY_MISMATCH")
        digest = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != digest:
            raise ValueError("UNIVERSE_SNAPSHOT_HASH_MISMATCH")
        object.__setattr__(self, "hash", digest)
        return self


def freeze_qualified_universe(
    reviews: tuple[EligibilityReview, ...],
    snapshots: tuple[ResearchSnapshot, ...],
    at: datetime,
    *,
    missing_reasons: Mapping[str, str] | None = None,
    source_universe_hash: str | None = None,
) -> QualifiedUniverseSnapshot:
    """Retain the complete admitted set, including members lacking data bundles."""
    if not reviews:
        raise ValueError("UNIVERSE_QUALIFIED_MEMBERS_REQUIRED")
    tickers = [review.metadata.ticker for review in reviews]
    broker_ids = [review.identifiers.trading212_id for review in reviews]
    if len(set(tickers)) != len(tickers) or len(set(broker_ids)) != len(broker_ids):
        raise ValueError("UNIVERSE_DUPLICATE_IDENTITY")
    index = {snapshot.ticker: snapshot for snapshot in snapshots}
    if len(index) != len(snapshots) or not set(index) <= set(tickers):
        raise ValueError("UNIVERSE_SNAPSHOT_MEMBERSHIP_MISMATCH")
    members = []
    for review in sorted(reviews, key=lambda item: item.metadata.ticker):
        review.identifiers.require_current(at)
        snapshot = index.get(review.metadata.ticker)
        expiry = min(
            review.identifiers.valid_until,
            review.metadata.verified_at + ELIGIBILITY_MAXIMUM_AGE,
        )
        reason = (missing_reasons or {}).get(review.metadata.ticker, "RESEARCH_EVIDENCE_REQUIRED")
        if snapshot is not None:
            if snapshot.instrument != review.metadata or snapshot.created_at > at:
                raise ValueError("UNIVERSE_SNAPSHOT_IDENTITY_MISMATCH")
            if snapshot.universe_hash is not None:
                raise ValueError("UNIVERSE_SNAPSHOT_ALREADY_BOUND")
            if not snapshot.evidence or any(
                record.fresh_until <= at
                or record.conflicting
                or not record.available_at(snapshot.cutoff_for(record))
                for record in snapshot.evidence
            ):
                raise ValueError("UNIVERSE_SNAPSHOT_EVIDENCE_INVALID")
            expiry = min(expiry, *(record.fresh_until for record in snapshot.evidence))
            reason = "READY"
        members.append(
            FrozenUniverseMember(
                instrument=review.metadata,
                qualification_hash=content_hash(review),
                valid_until=expiry,
                snapshot_hash=snapshot.hash if snapshot is not None else None,
                evidence_status=reason,
            )
        )
    return QualifiedUniverseSnapshot(
        observed_at=at,
        valid_until=min(member.valid_until for member in members),
        members=tuple(members),
        source_universe_hash=source_universe_hash,
    )


def bind_universe_snapshot(
    snapshot: ResearchSnapshot, universe: QualifiedUniverseSnapshot
) -> ResearchSnapshot:
    member = next(
        (member for member in universe.members if member.instrument.ticker == snapshot.ticker), None
    )
    if member is None or member.snapshot_hash != snapshot.hash:
        raise ValueError("UNIVERSE_SNAPSHOT_BINDING_MISMATCH")
    return ResearchSnapshot.model_validate(
        {**snapshot.model_dump(), "universe_hash": universe.hash, "hash": ""}
    )


class UniverseScreenPolicy(Contract):
    version: Literal["liquidity-attention-v1"] = "liquidity-attention-v1"
    maximum_candidates: int = Field(default=5, ge=1, le=20)
    observation_window: int = Field(default=20, ge=20, le=60)
    methodology: Literal[
        "Descending mean daily GBP traded value of the latest PIT-safe OHLCV window; "
        "ticker breaks ties. Attention allocation only, not expected return."
    ] = (
        "Descending mean daily GBP traded value of the latest PIT-safe OHLCV window; "
        "ticker breaks ties. Attention allocation only, not expected return."
    )


class UniverseScreenEntry(Contract):
    ticker: str
    snapshot_hash: str | None
    average_daily_value_gbp: Decimal | None = None
    evidence_ids: tuple[str, ...] = ()
    state: Literal["SELECTED", "OUTSIDE_ATTENTION_BOUND", "UNRESOLVED_DATA"]
    reason: str


class UniverseScreen(Contract):
    universe_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    policy: UniverseScreenPolicy
    methodology_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    entries: tuple[UniverseScreenEntry, ...]
    selected_tickers: tuple[str, ...]
    hash: str = ""

    @model_validator(mode="after")
    def auditable(self) -> Self:
        if self.methodology_hash != content_hash(self.policy):
            raise ValueError("UNIVERSE_SCREEN_METHODOLOGY_MISMATCH")
        if (
            self.selected_tickers
            != tuple(entry.ticker for entry in self.entries if entry.state == "SELECTED")
            or len(self.selected_tickers) > self.policy.maximum_candidates
        ):
            raise ValueError("UNIVERSE_SCREEN_BOUND_INVALID")
        if len({entry.ticker for entry in self.entries}) != len(self.entries):
            raise ValueError("UNIVERSE_SCREEN_DUPLICATE_IDENTITY")
        digest = content_hash(self.model_dump(mode="json", exclude={"hash"}))
        if self.hash and self.hash != digest:
            raise ValueError("UNIVERSE_SCREEN_HASH_MISMATCH")
        object.__setattr__(self, "hash", digest)
        return self


def screen_qualified_universe(
    universe: QualifiedUniverseSnapshot,
    snapshots: tuple[ResearchSnapshot, ...],
    *,
    at: datetime,
    policy: UniverseScreenPolicy | None = None,
) -> UniverseScreen:
    policy = policy or UniverseScreenPolicy()
    if not universe.observed_at <= at < universe.valid_until:
        raise ValueError("UNIVERSE_SCREEN_EXPIRED")
    index = {snapshot.ticker: snapshot for snapshot in snapshots}
    if len(index) != len(snapshots) or not set(index) <= {
        member.instrument.ticker for member in universe.members
    }:
        raise ValueError("UNIVERSE_SCREEN_MEMBERSHIP_MISMATCH")
    scores: list[tuple[Decimal, str, ResearchSnapshot, tuple[str, ...]]] = []
    unresolved = []
    for member in universe.members:
        snapshot = index.get(member.instrument.ticker)
        if member.snapshot_hash is None:
            if snapshot is not None:
                raise ValueError("UNIVERSE_SCREEN_EVIDENCE_NOT_FROZEN")
            unresolved.append(
                UniverseScreenEntry(
                    ticker=member.instrument.ticker,
                    snapshot_hash=None,
                    state="UNRESOLVED_DATA",
                    reason=member.evidence_status,
                )
            )
            continue
        if snapshot is None or snapshot.hash != member.snapshot_hash:
            raise ValueError("UNIVERSE_SCREEN_EVIDENCE_NOT_FROZEN")
        records = sorted(
            (record for record in snapshot.evidence if isinstance(record.payload, PriceBar)),
            key=lambda record: (record.observation_time, record.evidence_id),
        )[-policy.observation_window :]
        if (
            len(records) != policy.observation_window
            or any(
                record.fresh_until <= at
                or record.conflicting
                or not record.available_at(snapshot.price_cutoff)
                for record in records
            )
            or len({record.observation_time for record in records}) != len(records)
        ):
            unresolved.append(
                UniverseScreenEntry(
                    ticker=member.instrument.ticker,
                    snapshot_hash=snapshot.hash,
                    state="UNRESOLVED_DATA",
                    reason="SCREEN_CURRENT_PIT_HISTORY_REQUIRED",
                )
            )
            continue
        values = []
        for record in records:
            assert isinstance(record.payload, PriceBar)
            if record.payload.currency != snapshot.instrument.quote_currency:
                raise ValueError("UNIVERSE_SCREEN_CURRENCY_MISMATCH")
            values.append(
                normalize_gbp(record.payload.close, record.payload.currency).gbp
                * record.payload.volume
            )
        scores.append(
            (
                sum(values, Decimal(0)) / len(values),
                snapshot.ticker,
                snapshot,
                tuple(record.evidence_id for record in records),
            )
        )
    scores.sort(key=lambda item: (-item[0], item[1]))
    ranked = [
        UniverseScreenEntry(
            ticker=ticker,
            snapshot_hash=snapshot.hash,
            average_daily_value_gbp=score,
            evidence_ids=evidence_ids,
            state="SELECTED" if rank < policy.maximum_candidates else "OUTSIDE_ATTENTION_BOUND",
            reason="AUDITABLE_LIQUIDITY_ATTENTION_ORDER",
        )
        for rank, (score, ticker, snapshot, evidence_ids) in enumerate(scores)
    ]
    entries = tuple([*ranked, *sorted(unresolved, key=lambda item: item.ticker)])
    return UniverseScreen(
        universe_hash=universe.hash,
        policy=policy,
        methodology_hash=content_hash(policy),
        entries=entries,
        selected_tickers=tuple(entry.ticker for entry in ranked if entry.state == "SELECTED"),
    )


class UniverseResearchContext(Contract):
    """Durable full-universe evidence; qualitative firms still see only their stock."""

    universe: QualifiedUniverseSnapshot
    screen: UniverseScreen
    snapshots: tuple[ResearchSnapshot, ...]

    @model_validator(mode="after")
    def complete_frozen_context(self) -> Self:
        if self.screen.universe_hash != self.universe.hash:
            raise ValueError("UNIVERSE_CONTEXT_SCREEN_MISMATCH")
        if {entry.ticker for entry in self.screen.entries} != {
            member.instrument.ticker for member in self.universe.members
        }:
            raise ValueError("UNIVERSE_CONTEXT_MEMBERSHIP_MISMATCH")
        expected = {
            member.instrument.ticker: member.snapshot_hash
            for member in self.universe.members
            if member.snapshot_hash is not None
        }
        if (
            len(self.snapshots) != len(expected)
            or {snapshot.ticker: snapshot.hash for snapshot in self.snapshots} != expected
        ):
            raise ValueError("UNIVERSE_CONTEXT_EVIDENCE_INCOMPLETE")
        recomputed = screen_qualified_universe(
            self.universe,
            self.snapshots,
            at=self.universe.observed_at,
            policy=self.screen.policy,
        )
        if recomputed != self.screen:
            raise ValueError("UNIVERSE_CONTEXT_SCREEN_NOT_REPRODUCIBLE")
        return self


class UniverseSnapshotBuilder:
    """Live wrapper: whole current universe, then bounded numeric selection.

    The input ticker does not influence acquisition or ranking. The caller must
    persist ``context_for(snapshot)`` alongside a returned candidate snapshot;
    that context freezes the complete universe and all acquired evidence.
    No native/LLM work runs here, and failed stock data never gains admission.
    """

    def __init__(
        self,
        universe: Callable[[], tuple[InstrumentMetadata, ...]],
        reviews: Callable[[], tuple[EligibilityReview, ...]],
        build_snapshot: Callable[[InstrumentMetadata], ResearchSnapshot],
        *,
        clock: Callable[[], datetime] = utc_now,
        policy: UniverseScreenPolicy | None = None,
        validate_sources: Callable[[], None] | None = None,
    ) -> None:
        self._universe = universe
        self._reviews = reviews
        self._build_snapshot = build_snapshot
        self._clock = clock
        self._policy = policy or UniverseScreenPolicy()
        self._validate_sources = validate_sources
        self._context: UniverseResearchContext | None = None
        self._lock = threading.RLock()

    @property
    def last_universe(self) -> QualifiedUniverseSnapshot | None:
        return self._context.universe if self._context is not None else None

    @property
    def last_screen(self) -> UniverseScreen | None:
        return self._context.screen if self._context is not None else None

    @property
    def last_snapshots(self) -> tuple[ResearchSnapshot, ...]:
        return self._context.snapshots if self._context is not None else ()

    def __call__(self, instrument: InstrumentMetadata) -> ResearchSnapshot:
        with self._lock:
            if self._validate_sources is not None:
                self._validate_sources()
            current = self._universe()
            reviews = self._reviews()
            index = {review.metadata.ticker: review for review in reviews}
            if len(index) != len(reviews) or len({item.ticker for item in current}) != len(current):
                raise ValueError("UNIVERSE_DUPLICATE_IDENTITY")
            admitted = []
            for item in current:
                review = index.get(item.ticker)
                if review is None or review.metadata != item:
                    raise ValueError("UNIVERSE_LIVE_REVIEW_MISMATCH")
                admitted.append(review)
            if not admitted:
                raise ValueError("UNIVERSE_QUALIFIED_MEMBERS_REQUIRED")
            now = self._clock()
            for review in admitted:
                review.identifiers.require_current(now)
                if eligibility_failures(review.metadata, ResearchMandate(), now):
                    raise ValueError("UNIVERSE_MEMBER_NOT_QUALIFIED")
            current_hashes = {review.metadata.ticker: content_hash(review) for review in admitted}
            context = self._context
            if (
                context is None
                # Without an independent current provider/source check, never
                # let a cache bypass gates performed inside the data builder.
                or self._validate_sources is None
                or not (
                    context.universe.observed_at
                    <= now
                    < min(
                        context.universe.valid_until,
                        context.universe.observed_at + timedelta(minutes=10),
                    )
                )
                or current_hashes
                != {
                    member.instrument.ticker: member.qualification_hash
                    for member in context.universe.members
                }
            ):
                self._context = None
                evidence = []
                missing: dict[str, str] = {}
                for review in sorted(admitted, key=lambda item: item.metadata.ticker):
                    try:
                        value = self._build_snapshot(review.metadata)
                        if value.instrument != review.metadata or value.universe_hash is not None:
                            raise ValueError("UNIVERSE_LIVE_SNAPSHOT_MISMATCH")
                        evidence.append(value)
                    except (ValueError, OSError, TimeoutError, ProviderFailure) as error:
                        # No provider exception strings (potential credentials)
                        # may enter the persisted failure projection.
                        missing[review.metadata.ticker] = (
                            "RESEARCH_PROVIDER_UNAVAILABLE"
                            if isinstance(error, ProviderFailure)
                            else "RESEARCH_EVIDENCE_NOT_VERIFIED"
                        )
                frozen_at = self._clock()
                fresh_reviews = tuple(
                    review
                    for review in admitted
                    if not eligibility_failures(review.metadata, ResearchMandate(), frozen_at)
                    and review.identifiers.verified_at <= frozen_at < review.identifiers.valid_until
                )
                fresh_tickers = {review.metadata.ticker for review in fresh_reviews}
                ready = []
                for value in evidence:
                    if value.ticker not in fresh_tickers:
                        continue
                    if (
                        value.created_at > frozen_at
                        or not value.evidence
                        or any(
                            record.fresh_until <= frozen_at
                            or record.conflicting
                            or not record.available_at(value.cutoff_for(record))
                            for record in value.evidence
                        )
                    ):
                        missing[value.ticker] = "RESEARCH_CURRENT_EVIDENCE_REQUIRED"
                    else:
                        ready.append(value)
                frozen = freeze_qualified_universe(
                    fresh_reviews, tuple(ready), frozen_at, missing_reasons=missing
                )
                screen = screen_qualified_universe(
                    frozen, tuple(ready), at=frozen_at, policy=self._policy
                )
                context = UniverseResearchContext(
                    universe=frozen, screen=screen, snapshots=tuple(ready)
                )
                self._context = context
            if instrument.ticker not in context.screen.selected_tickers:
                raise ValueError("UNIVERSE_ATTENTION_BOUND_NOT_SELECTED")
            selected = next(
                value for value in context.snapshots if value.ticker == instrument.ticker
            )
            if selected.instrument != instrument:
                raise ValueError("UNIVERSE_REQUESTED_IDENTITY_MISMATCH")
            return bind_universe_snapshot(selected, context.universe)

    def context_for(self, snapshot: ResearchSnapshot) -> UniverseResearchContext:
        with self._lock:
            context = self._context
            if context is None or snapshot.universe_hash != context.universe.hash:
                raise ValueError("UNIVERSE_RESEARCH_CONTEXT_UNAVAILABLE")
            if snapshot.ticker not in context.screen.selected_tickers:
                raise ValueError("UNIVERSE_ATTENTION_BOUND_NOT_SELECTED")
            original = next(value for value in context.snapshots if value.ticker == snapshot.ticker)
            if bind_universe_snapshot(original, context.universe) != snapshot:
                raise ValueError("UNIVERSE_RESEARCH_CONTEXT_MISMATCH")
            return context
