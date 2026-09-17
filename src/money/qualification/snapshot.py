"""Freeze genuine admitted provider data without manufacturing a manifest."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from money.adapters.eligibility import eligibility_failures
from money.data.live_eligibility import EligibilityReview
from money.data.qualification import ProviderQualification
from money.data.quality.market import evaluate_market_quality
from money.data.security import ProviderFailure
from money.data.uk.filing_documents import ReviewedStorageHost
from money.qualification.core import QualificationContext, fingerprint
from money.research.live import LiveSnapshotBuilder, VerifiedInstrument
from money.scanner.universe import (
    bind_universe_snapshot,
    freeze_qualified_universe,
    screen_qualified_universe,
)
from money.schemas.contracts import Contract, EvidenceRecord, ResearchMandate, ResearchSnapshot
from money.storage import ResearchStore
from money.storage.models import queue_control
from money.storage.production_models import provider_state


class QualificationSources(Contract):
    reviewed_instruments: tuple[VerifiedInstrument, ...]
    provider_qualifications: tuple[ProviderQualification, ...]
    market_credential_environment_variable: str = "EODHD_API_KEY"
    filings_credential_environment_variable: str = "COMPANIES_HOUSE_API_KEY"
    filing_document_storage_hosts: tuple[ReviewedStorageHost, ...] = ()


def _cached_snapshot(
    ctx: QualificationContext, cached: dict[str, Any] | None
) -> ResearchSnapshot | None:
    """Read the frozen artifact, never a mutable checkpoint copy of its payload."""
    if not cached:
        return None
    reference = cached.get("snapshot_artifact")
    if (
        not isinstance(reference, (list, tuple))
        or len(reference) != 2
        or not all(isinstance(item, str) for item in reference)
    ):
        return None
    return ResearchSnapshot.model_validate_json(ctx.verify_artifact(*reference))


def _refreeze(ctx: QualificationContext, previous: ResearchSnapshot) -> ResearchSnapshot:
    """New envelope after honest review; never alter economic/availability times."""
    snapshot_id = str(uuid4())
    evidence = tuple(
        EvidenceRecord.model_validate(
            {
                **item.model_dump(),
                "snapshot_id": snapshot_id,
                "hash": "",
            }
        )
        for item in previous.evidence
    )
    return ResearchSnapshot.model_validate(
        {
            **previous.model_dump(),
            "snapshot_id": snapshot_id,
            "created_at": ctx.now,
            "price_cutoff": ctx.now,
            "news_cutoff": ctx.now,
            "filing_cutoff": ctx.now,
            "fundamental_cutoff": ctx.now,
            "evidence": evidence,
            "hash": "",
        }
    )


def _instrument_snapshot(
    ctx: QualificationContext, sources: QualificationSources, selected: VerifiedInstrument
) -> ResearchSnapshot:
    """Resume one genuine data acquisition, never an earlier universe selection."""
    selected.identifiers.require_current(ctx.now)
    study = ctx.read_bytes("reviews/lean-inputs.json") or b""
    source_key = fingerprint(
        {
            "instrument": selected.model_dump(mode="json"),
            "providers": [item.model_dump(mode="json") for item in sources.provider_qualifications],
            "storage_hosts": [
                item.model_dump(mode="json") for item in sources.filing_document_storage_hosts
            ],
        }
    )
    namespace = "snapshot-" + hashlib.sha256(selected.metadata.ticker.encode()).hexdigest()[:24]
    key = fingerprint({"sources": source_key, "lean_review": hashlib.sha256(study).hexdigest()})
    cached = _cached_snapshot(ctx, ctx.cache(namespace, key, 3600))
    if cached is not None:
        snapshot = cached
        if snapshot.instrument == selected.metadata and all(
            item.available_at(ctx.now) and item.fresh_until > ctx.now for item in snapshot.evidence
        ):
            return snapshot
    original = _cached_snapshot(ctx, ctx.cache(namespace + "-source", source_key, 3600))
    if original is not None:
        previous = original
        if previous.instrument == selected.metadata and all(
            item.available_at(ctx.now) and item.fresh_until > ctx.now for item in previous.evidence
        ):
            snapshot = _refreeze(ctx, previous)
            quality = evaluate_market_quality(
                snapshot,
                ctx.now,
                spread_bps=selected.spread_bps,
                adjustment_basis="RAW",
                corporate_actions_complete=selected.corporate_actions_complete,
            )
            if quality.passed:
                value = snapshot.model_dump(mode="json")
                reference = ctx.artifact(value)
                ctx.artifact(
                    {
                        "kind": "refrozen-unchanged-fresh-evidence",
                        "prior_snapshot_hash": previous.hash,
                        "snapshot_hash": snapshot.hash,
                        "publication_and_retrieval_times_unchanged": True,
                    }
                )
                ctx.checkpoint(namespace, key, {"snapshot_artifact": reference}, [reference])
                # Do not renew the source checkpoint or its acquisition expiry.
                return snapshot
    # Only the circuit-breaker tables exist in this ephemeral in-memory store.
    # It is not a job queue, model registry, production database or qualification.
    store = ResearchStore("sqlite:///:memory:", allow_sqlite=True)
    try:
        queue_control.create(store.engine)
        provider_state.create(store.engine)
        with store.engine.begin() as connection:
            connection.execute(queue_control.insert().values(id=1))
        snapshot = LiveSnapshotBuilder(sources, store)(selected.metadata)
    finally:
        store.engine.dispose()
    if snapshot.instrument.provider == "money-demo" or any(
        item.provider == "money-demo" for item in snapshot.evidence
    ):
        raise ValueError("QUALIFICATION_SYNTHETIC_EVIDENCE_FORBIDDEN")
    value = snapshot.model_dump(mode="json")
    reference = ctx.artifact(value)
    ctx.checkpoint(namespace + "-source", source_key, {"snapshot_artifact": reference}, [reference])
    ctx.checkpoint(namespace, key, {"snapshot_artifact": reference}, [reference])
    return snapshot


def run_snapshot_stage(
    ctx: QualificationContext, provider_output: dict[str, Any]
) -> dict[str, Any]:
    """Freeze complete admitted membership/data before bounded numeric screening.

    Missing data excludes a stock from expensive native work, not from the
    record of admitted universe membership. It does not veto unrelated stocks.
    """
    fields = provider_output.get("manifest_fields", provider_output)
    sources = QualificationSources.model_validate(
        {
            "reviewed_instruments": fields.get("instruments", []),
            "provider_qualifications": fields.get("provider_qualifications", []),
            "filing_document_storage_hosts": fields.get("filing_document_storage_hosts", []),
        }
    )
    reviews = tuple(
        EligibilityReview.model_validate(item)
        for item in provider_output.get(
            "qualified_universe",
            [
                item.model_dump(
                    include={
                        "metadata",
                        "identifiers",
                        "eligibility_proof_hash",
                        "ethical_proof_hash",
                    }
                )
                for item in sources.reviewed_instruments
            ],
        )
    )
    current = []
    expired = []
    for review in reviews:
        try:
            review.identifiers.require_current(ctx.now)
            if eligibility_failures(review.metadata, ResearchMandate(), ctx.now):
                raise ValueError("SNAPSHOT_INSTRUMENT_INELIGIBLE")
        except ValueError:
            expired.append(review.metadata.ticker)
        else:
            current.append(review)
    if not current:
        ctx.block(
            "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED",
            "Refresh the complete live universe; no current eligibility-qualified GBP/GBX stock is admitted.",
        )
        return {"complete": False, "qualified_count": 0, "expired_or_ineligible": sorted(expired)}
    source_universe_hash = None
    universe_reference = provider_output.get("universe_artifact")
    if universe_reference is not None:
        if not isinstance(universe_reference, (tuple, list)) or len(universe_reference) != 2:
            raise ValueError("SNAPSHOT_UNIVERSE_ARTIFACT_INVALID")
        ctx.verify_artifact(*universe_reference)
        source_universe_hash = universe_reference[0]
    provider_current = True
    try:
        if not sources.provider_qualifications:
            raise ValueError("SNAPSHOT_PROVIDER_QUALIFICATION_REQUIRED")
        for provider in sources.provider_qualifications:
            for dataset in provider.datasets:
                provider.require(dataset, ctx.now)
    except ValueError:
        provider_current = False
    details = {item.metadata.ticker: item for item in sources.reviewed_instruments}
    if len(details) != len(sources.reviewed_instruments):
        raise ValueError("SNAPSHOT_DUPLICATE_IDENTITY")
    snapshots = []
    missing = {}
    allowed_reasons = {
        "CRITICAL_FUNDAMENTALS_MISSING",
        "PROVIDER_UNQUALIFIED",
        "PROVIDER_COVERAGE_MISSING",
        "ARCHIVED_MARKET_PROVIDER_UNQUALIFIED",
        "PROVIDER_HISTORICAL_AVAILABILITY_UNKNOWN",
        "MARKET_QUALITY_FAILED",
        "CRITICAL_DATA_STALE",
        "SPREAD_EVIDENCE_MISMATCH",
        "QUALIFICATION_SYNTHETIC_EVIDENCE_FORBIDDEN",
    }
    for review in sorted(current, key=lambda item: item.metadata.ticker):
        ticker = review.metadata.ticker
        selected = details.get(ticker)
        if selected is None or not provider_current:
            missing[ticker] = "RESEARCH_PROVIDER_EVIDENCE_REQUIRED"
            continue
        if selected.metadata != review.metadata or selected.identifiers != review.identifiers:
            missing[ticker] = "RESEARCH_QUALIFIED_IDENTITY_MISMATCH"
            continue
        try:
            snapshots.append(_instrument_snapshot(ctx, sources, selected))
        except (ValueError, OSError, TimeoutError, ProviderFailure) as error:
            reason = str(error).partition(":")[0]
            missing[ticker] = (
                "RESEARCH_PROVIDER_UNAVAILABLE"
                if isinstance(error, ProviderFailure)
                else reason
                if reason in allowed_reasons
                else "RESEARCH_EVIDENCE_NOT_VERIFIED"
            )
    frozen_at = max([ctx.now, *(snapshot.created_at for snapshot in snapshots)])
    # Acquisition can cross a review expiry. Expired members are not admitted
    # at the final time T; retain an explicit audit of their removal.
    fresh = []
    for review in current:
        if eligibility_failures(review.metadata, ResearchMandate(), frozen_at) or not (
            review.identifiers.verified_at <= frozen_at < review.identifiers.valid_until
        ):
            expired.append(review.metadata.ticker)
        else:
            fresh.append(review)
    if not fresh:
        ctx.block(
            "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED",
            "Universe evidence expired during acquisition; refresh before research.",
        )
        return {"complete": False, "qualified_count": 0, "expired_or_ineligible": sorted(expired)}
    fresh_tickers = {review.metadata.ticker for review in fresh}
    ready = []
    for snapshot in snapshots:
        if snapshot.ticker not in fresh_tickers:
            continue
        if any(
            record.fresh_until <= frozen_at
            or record.conflicting
            or not record.available_at(snapshot.cutoff_for(record))
            for record in snapshot.evidence
        ):
            missing[snapshot.ticker] = "CRITICAL_DATA_STALE"
            continue
        ready.append(snapshot)
    universe = freeze_qualified_universe(
        tuple(fresh),
        tuple(ready),
        frozen_at,
        missing_reasons=missing,
        source_universe_hash=source_universe_hash,
    )
    references = {
        snapshot.ticker: ctx.artifact(snapshot.model_dump(mode="json")) for snapshot in ready
    }
    universe_value = universe.model_dump(mode="json")
    universe_artifact = ctx.artifact(universe_value)
    ctx.write_json("outputs/universe-snapshot.json", universe_value)
    ctx.write_json(
        "outputs/universe-snapshot-evidence.json",
        {
            "universe_hash": universe.hash,
            "snapshot_artifacts": references,
            "expired_or_ineligible": sorted(set(expired)),
        },
    )
    screen = screen_qualified_universe(universe, tuple(ready), at=frozen_at)
    screen_value = screen.model_dump(mode="json")
    screen_artifact = ctx.artifact(screen_value)
    ctx.write_json("outputs/universe-screen.json", screen_value)
    index = {snapshot.ticker: snapshot for snapshot in ready}
    candidates = []
    for ticker in screen.selected_tickers:
        bound = bind_universe_snapshot(index[ticker], universe)
        path = (
            "outputs/candidate-snapshots/"
            + hashlib.sha256(ticker.encode()).hexdigest()[:24]
            + ".json"
        )
        reference = ctx.artifact(bound.model_dump(mode="json"))
        ctx.write_json(path, bound.model_dump(mode="json"))
        candidates.append(
            {
                "ticker": ticker,
                "snapshot_hash": bound.hash,
                "snapshot_path": path,
                "snapshot_artifact": reference,
            }
        )
    if candidates:
        # Compatibility admission sample for existing native/LEAN qualification
        # contracts; it never defines or truncates the frozen universe.
        ctx.write_bytes(
            "outputs/snapshot.json", ctx.verify_artifact(*candidates[0]["snapshot_artifact"])
        )
    else:
        ctx.block(
            "SNAPSHOT_REVIEWED_DATA_REQUIRED",
            "Full membership is frozen; see outputs/universe-screen.json for missing per-stock provider, PIT, spread, financial or current-history evidence.",
        )
    return {
        "complete": bool(candidates),
        "qualified_count": len(universe.members),
        "qualified_universe_hash": universe.hash,
        "universe_artifact": universe_artifact,
        "screen_artifact": screen_artifact,
        "candidate_snapshots": candidates,
        "unresolved_data": missing,
        "expired_or_ineligible": sorted(set(expired)),
        **(
            {
                "snapshot_hash": candidates[0]["snapshot_hash"],
                "snapshot_path": "outputs/snapshot.json",
            }
            if candidates
            else {}
        ),
    }
