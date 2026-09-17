"""Bounded full-universe eligibility release artifacts, not research selection."""

from collections.abc import Sequence
from typing import Any

from pydantic import TypeAdapter

from money.adapters.eligibility import eligibility_failures
from money.data.live_eligibility import EligibilityReview
from money.qualification.core import QualificationContext, json_bytes
from money.schemas.contracts import ResearchMandate


def eligibility_catalogs(
    ctx: QualificationContext, raw_reviews: Sequence[Any]
) -> tuple[tuple[str, str], ...]:
    """Preserve every current eligible member, irrespective of supplemental inputs.

    Chunking changes only packaging. It neither manufactures review evidence nor
    relaxes the existing per-artifact limit. The release review pins all returned
    chunk hashes; ``load_manifest`` separately verifies every referenced proof.
    """
    reviews = TypeAdapter(tuple[EligibilityReview, ...]).validate_python(raw_reviews)
    if not reviews or len(reviews) > 100000:
        raise ValueError("QUALIFICATION_ELIGIBILITY_CATALOG_SIZE_INVALID")
    seen: set[str] = set()
    broker_ids: set[str] = set()
    economic_ids: set[tuple[str, str, str]] = set()
    for review in reviews:
        identity = (review.identifiers.isin, review.identifiers.exchange,
                    review.identifiers.quote_currency)
        if (review.metadata.ticker in seen or review.identifiers.trading212_id in broker_ids
                or identity in economic_ids):
            raise ValueError("QUALIFICATION_ELIGIBILITY_CATALOG_DUPLICATE")
        seen.add(review.metadata.ticker)
        broker_ids.add(review.identifiers.trading212_id)
        economic_ids.add(identity)
        review.identifiers.require_current(ctx.now)
        if eligibility_failures(review.metadata, ResearchMandate(), ctx.now):
            raise ValueError("QUALIFICATION_ELIGIBILITY_CATALOG_NOT_CURRENT")
    chunks: list[tuple[str, str]] = []
    chunk: list[dict[str, Any]] = []
    estimated_bytes = 2
    for review in sorted(reviews, key=lambda value: (value.metadata.ticker, value.identifiers.trading212_id)):
        row = review.model_dump(mode="json")
        # An extra indentation level adds two bytes per serialized line.
        serialized = json_bytes(row)
        size = len(serialized) + serialized.count(b"\n") * 2 + 2
        if size > 1_500_000:
            raise ValueError("QUALIFICATION_ELIGIBILITY_ROW_SIZE_INVALID")
        if chunk and (len(chunk) >= 256 or estimated_bytes + size > 1_500_000):
            chunks.append(ctx.artifact(chunk))
            chunk, estimated_bytes = [], 2
        chunk.append(row)
        estimated_bytes += size
    if chunk:
        chunks.append(ctx.artifact(chunk))
    if len(chunks) > 512:
        raise ValueError("QUALIFICATION_ELIGIBILITY_CATALOG_SIZE_INVALID")
    return tuple(chunks)
