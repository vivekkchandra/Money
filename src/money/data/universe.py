"""Bounded qualified GBX stock subset, not the complete accessible broker universe.

This service consumes the separately qualified, hashed Money live manifest. It
does not assert account type or purchase availability, fetch an account, broaden
the mandate, or promote public R&D listings.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, field_validator

from money.adapters.eligibility import ELIGIBILITY_MAXIMUM_AGE
from money.data.instruments import InstrumentCatalogue, InstrumentSearchResult, ReviewedInstrument
from money.schemas.contracts import Contract, ResearchMandate, content_hash


class StockUniverseQuery(Contract):
    query: str = Field(default="", max_length=80, pattern=r"^[^\x00-\x1f\x7f]*$")
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0, le=10000)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return value.strip()


class StockUniverseInstrument(InstrumentSearchResult):
    eligibility: Literal["VERIFIED_ELIGIBLE"] = "VERIFIED_ELIGIBLE"
    research_allowed: Literal[True] = True
    synthetic: Literal[False] = False
    currency: Literal["GBP", "GBX"]
    instrument_type: Literal["STOCK"] = "STOCK"
    source: str
    provider: str
    source_id: str
    metadata_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    eligibility_proof_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    ethical_proof_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_until: AwareDatetime


class StockUniversePage(Contract):
    instruments: tuple[StockUniverseInstrument, ...]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=50)
    offset: int = Field(ge=0, le=10000)
    coverage: Literal["reviewed_manifest"] = "reviewed_manifest"
    complete_broker_universe: Literal[False] = False
    mode: Literal["live"] = "live"
    evaluated_at: AwareDatetime
    catalogue_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReviewedStockUniverse:
    """Read-time eligibility checks over an immutable reviewed release catalogue.

    The caller must use the validated manifest loader and retain its commercial
    data-rights checks. This view does not grant provider or redistribution rights.
    """

    def __init__(self, catalogue: InstrumentCatalogue) -> None:
        if catalogue.mode != "live" or not catalogue.qualifications:
            raise ValueError("UNIVERSE_UNAVAILABLE")
        self._catalogue = catalogue
        self._hash = content_hash(
            {
                "entries": [
                    {
                        "metadata": item.metadata.model_dump(mode="json"),
                        "identifiers": item.identifiers.model_dump(mode="json")
                        if item.identifiers
                        else None,
                        "eligibility_proof_hash": item.eligibility_proof_hash,
                        "ethical_proof_hash": item.ethical_proof_hash,
                    }
                    for item in sorted(catalogue.entries, key=lambda entry: entry.metadata.ticker)
                ],
                "qualifications": [
                    item.model_dump(mode="json")
                    for item in sorted(catalogue.qualifications, key=lambda entry: entry.provider)
                ],
            }
        )

    @staticmethod
    def _proofs_present(item: ReviewedInstrument) -> bool:
        return all(
            isinstance(digest, str) and re.fullmatch(r"[a-f0-9]{64}", digest)
            for digest in (item.eligibility_proof_hash, item.ethical_proof_hash)
        )

    def page(
        self,
        query: StockUniverseQuery,
        *,
        mandate: ResearchMandate,
        now: datetime,
    ) -> StockUniversePage:
        """Return only current, verified, mandate-compliant reviewed instruments."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("ELIGIBILITY_TIMESTAMP_INVALID")
        self._catalogue.require_current(now)
        entries = sorted(
            (
                item
                for item in self._catalogue.entries
                if self._proofs_present(item)
                and item.identifiers is not None
                and not item.failures(mandate, now)
                and (not query.query or item.matches(query.query))
            ),
            key=lambda item: (item.metadata.ticker != query.query.upper(), item.metadata.ticker),
        )
        instruments = []
        for item in entries[query.offset : query.offset + query.limit]:
            assert item.identifiers is not None
            assert item.eligibility_proof_hash is not None and item.ethical_proof_hash is not None
            metadata = item.metadata
            # Proof admission was checked above. Never add time to the original
            # verification on cache reads, and respect earlier mapping expiry.
            expiry = min(
                metadata.verified_at + ELIGIBILITY_MAXIMUM_AGE,
                item.identifiers.valid_until,
                *(qualification.valid_until for qualification in self._catalogue.qualifications),
            )
            if metadata.ethical_clearance is not None:
                expiry = min(expiry, metadata.ethical_clearance.valid_until)
            instruments.append(
                StockUniverseInstrument(
                    **item.result(now, synthetic=False).model_dump(exclude={"instrument_type"}),
                    instrument_type="STOCK",
                    source=metadata.source,
                    provider=metadata.provider,
                    source_id=metadata.source_id,
                    metadata_hash=content_hash(metadata.model_dump(mode="json")),
                    eligibility_proof_hash=item.eligibility_proof_hash,
                    ethical_proof_hash=item.ethical_proof_hash,
                    verified_until=expiry,
                )
            )
        return StockUniversePage(
            instruments=tuple(instruments),
            total=len(entries),
            limit=query.limit,
            offset=query.offset,
            evaluated_at=now,
            catalogue_hash=self._hash,
        )


# Import compatibility only. None of these aliases asserts ISA qualification.
IsaUniverseQuery = StockUniverseQuery
IsaUniverseInstrument = StockUniverseInstrument
IsaUniversePage = StockUniversePage
ReviewedIsaUniverse = ReviewedStockUniverse
