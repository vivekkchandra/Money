"""Persist research-reference observations separately from manually recorded trades."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from sqlalchemy import select

from money.data.normalization.prices import normalize_gbp
from money.performance.outcomes import OutcomeBar, OutcomeSpecification, calculate_outcome
from money.schemas.contracts import PriceBar, ResearchSignal, ResearchSnapshot
from money.signals.generation import SignalDesign
from money.storage import ResearchStore
from money.storage import models as db
from money.storage.store import StoreError, digest, now_utc


def evaluate_signal_outcome(
    store: ResearchStore,
    job_id: str,
    bars: tuple[OutcomeBar, ...],
    as_of: datetime,
    *,
    adjustment_basis: Literal["UNADJUSTED_NO_ACTIONS", "SPLIT_ADJUSTED", "TOTAL_RETURN"],
    dataset_version: str,
) -> dict:
    if adjustment_basis != "UNADJUSTED_NO_ACTIONS":
        raise StoreError(
            "Adjusted outcomes require a common basis with the original reference price"
        )
    job = store.get_job(job_id)
    if job is None or not dataset_version or len(dataset_version) > 200:
        raise StoreError("Outcome requires an accessible published research signal and dataset")
    with store.engine.connect() as connection:
        stored = connection.scalar(
            select(db.signals.c.payload).where(db.signals.c.job_id == job_id)
        )
        frozen = connection.scalar(
            select(db.snapshots.c.payload).where(db.snapshots.c.job_id == job_id)
        )
        design_data = connection.scalar(
            select(db.artifacts.c.payload).where(
                db.artifacts.c.job_id == job_id,
                db.artifacts.c.kind == "signal_design",
            )
        )
    if stored is None or frozen is None:
        raise StoreError("No published research signal is available for outcome evaluation")
    signal = ResearchSignal.model_validate(stored)
    snapshot = ResearchSnapshot.model_validate(frozen)
    prices = sorted(
        (record for record in snapshot.evidence if isinstance(record.payload, PriceBar)),
        key=lambda record: record.observation_time,
    )
    if not prices or not isinstance(prices[-1].payload, PriceBar):
        raise StoreError("Original reference price is unavailable")
    reference = normalize_gbp(prices[-1].payload.close, prices[-1].payload.currency).gbp
    targets = tuple(
        normalize_gbp(value, signal.quote_currency).gbp for value in signal.potential_targets
    )
    target = min((value for value in targets if value > reference), default=None)
    design = SignalDesign.model_validate(design_data) if design_data is not None else None
    if design is not None and design.signal != signal:
        raise StoreError("Signal design differs from the published research signal")
    invalidation = design.invalidation_gbp if design is not None else None
    specification = OutcomeSpecification(
        research_id=job_id,
        ticker=signal.ticker,
        issued_at=signal.issued_at,
        reference_price_gbp=reference,
        target_gbp=target,
        invalidation_gbp=invalidation,
    )
    result = calculate_outcome(specification, bars, as_of).model_dump(mode="json")
    result.update(
        {
            "dataset_version": dataset_version,
            "adjustment_basis": adjustment_basis,
            "reference_evidence_id": prices[-1].evidence_id,
            "calibration_qualified": False,
            "assumed_capital_gbp": str(signal.assumed_capital_gbp),
            "illustrative_allocation_gbp": str(signal.illustrative_allocation_gbp),
            "hypothetical_gbp_outcomes": [
                {
                    "calendar_days": item["calendar_days"],
                    "state": item["state"],
                    "price_change_gbp": str(
                        signal.illustrative_allocation_gbp * Decimal(item["return_fraction"])
                    ) if item["return_fraction"] is not None else None,
                    "basis": "RESEARCH_REFERENCE_PRICE_CHANGE_EXCLUDING_COSTS",
                }
                for item in result["horizons"]
            ],
            "limitations": [
                *result["limitations"],
                "GBP outcomes use only the published illustrative allocation, not a broker balance or an assumed executed position.",
                "Numeric invalidation comes from the immutable signal design when available; otherwise it is unknown.",
                "Operator-supplied observations require independent provider and corporate-action verification before calibration.",
            ],
        }
    )
    record_id = digest({"research_id": job_id, "outcome": result})
    with store.transaction() as connection:
        connection.execute(select(db.queue_control).with_for_update()).all()
        if connection.scalar(select(db.outcomes.c.id).where(db.outcomes.c.id == record_id)) is None:
            connection.execute(
                db.outcomes.insert().values(
                    id=record_id, job_id=job_id, payload=result, created_at=now_utc()
                )
            )
    return result
