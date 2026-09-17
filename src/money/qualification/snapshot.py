"""Freeze genuine admitted provider data without manufacturing a manifest."""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

from money.adapters.eligibility import eligibility_failures
from money.data.qualification import ProviderQualification
from money.data.quality.market import evaluate_market_quality
from money.data.uk.filing_documents import ReviewedStorageHost
from money.qualification.core import QualificationContext, fingerprint
from money.research.live import LiveSnapshotBuilder, VerifiedInstrument
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


def run_snapshot_stage(
    ctx: QualificationContext, provider_output: dict[str, Any]
) -> dict[str, Any]:
    """Reuse a fresh frozen snapshot only while all input reviews still match."""
    fields = provider_output.get("manifest_fields", provider_output)
    inputs = {
        "reviewed_instruments": fields.get("instruments", []),
        "provider_qualifications": fields.get("provider_qualifications", []),
        "filing_document_storage_hosts": fields.get("filing_document_storage_hosts", []),
    }
    if not inputs["reviewed_instruments"] or not inputs["provider_qualifications"]:
        ctx.block(
            "SNAPSHOT_REVIEWED_DATA_REQUIRED",
            "Complete discovered instrument and provider reviews; the runner will then freeze live evidence automatically.",
        )
        return {}
    sources = QualificationSources.model_validate(inputs)
    current = tuple(
        item
        for item in sources.reviewed_instruments
        if not eligibility_failures(item.metadata, ResearchMandate(), ctx.now)
    )
    if not current:
        ctx.block(
            "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED",
            "Refresh eligibility and identifier reviews for at least one current GBP/GBX stock.",
        )
        return {}
    # Deterministic selection, preferring an instrument with reviewed filings.
    selected = sorted(
        current, key=lambda item: (not bool(item.filing_documents), item.metadata.ticker)
    )[0]
    selected.identifiers.require_current(ctx.now)
    for provider in sources.provider_qualifications:
        for dataset in provider.datasets:
            provider.require(dataset, ctx.now)
    study = ctx.read_bytes("reviews/lean-inputs.json") or b""
    source_key = fingerprint(inputs)
    key = fingerprint({"sources": source_key, "lean_review": hashlib.sha256(study).hexdigest()})
    cached = _cached_snapshot(ctx, ctx.cache("snapshot", key, 3600))
    if cached is not None:
        snapshot = cached
        if snapshot.instrument == selected.metadata and all(
            item.available_at(ctx.now) and item.fresh_until > ctx.now for item in snapshot.evidence
        ):
            ctx.write_json("outputs/snapshot.json", snapshot.model_dump(mode="json"))
            return {
                "complete": True,
                "snapshot_hash": snapshot.hash,
                "snapshot_path": "outputs/snapshot.json",
            }
    original = _cached_snapshot(ctx, ctx.cache("snapshot-source", source_key, 3600))
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
                ctx.write_json("outputs/snapshot.json", value)
                ctx.checkpoint("snapshot", key, {"snapshot_artifact": reference}, [reference])
                # Do not renew the source checkpoint or its acquisition expiry.
                return {
                    "complete": True,
                    "snapshot_hash": snapshot.hash,
                    "snapshot_path": "outputs/snapshot.json",
                }
    # Only the circuit-breaker tables exist in this ephemeral in-memory store.
    # It is not a job queue, model registry, production database or qualification.
    store = ResearchStore("sqlite:///:memory:", allow_sqlite=True)
    try:
        queue_control.create(store.engine)
        provider_state.create(store.engine)
        with store.engine.begin() as connection:
            connection.execute(queue_control.insert().values(id=1))
        try:
            snapshot = LiveSnapshotBuilder(sources, store)(selected.metadata)
        except ValueError as error:
            # Only repository-owned, allowlisted codes may leave the boundary.
            reason = str(error).partition(":")[0]
            actions = {
                "CRITICAL_FUNDAMENTALS_MISSING": "Complete financial-document or independently qualified financial-data review; filing history alone contains no qualified financial facts.",
                "PROVIDER_UNQUALIFIED": "Complete provider qualification for every supplemental/spread evidence source; a supplied EvidenceRecord is not provider admission.",
                "PROVIDER_COVERAGE_MISSING": "Supply current reviewed provider coverage for every required dataset, including financial/spread facts.",
                "ARCHIVED_MARKET_PROVIDER_UNQUALIFIED": "Provide independently qualified original-publication archive provenance; current API retrieval is not historical PIT proof.",
                "PROVIDER_HISTORICAL_AVAILABILITY_UNKNOWN": "Review genuine archived original-publication availability; today's backfilled bars cannot establish point-in-time history.",
                "MARKET_QUALITY_FAILED": "Provide live evidence meeting the existing market-quality contract, including adequate genuinely PIT-safe history, current spread and corporate-action coverage.",
                "CRITICAL_DATA_STALE": "Refresh expired supplemental evidence and its review; the runner does not extend evidence freshness.",
                "SPREAD_EVIDENCE_MISMATCH": "Correct independently observed spread_bps evidence and its units so it matches the reviewed instrument spread.",
            }
            if reason not in actions:
                raise
            ctx.block(reason, actions[reason])
            return {}
    finally:
        store.engine.dispose()
    if snapshot.instrument.provider == "money-demo" or any(
        item.provider == "money-demo" for item in snapshot.evidence
    ):
        raise ValueError("QUALIFICATION_SYNTHETIC_EVIDENCE_FORBIDDEN")
    value = snapshot.model_dump(mode="json")
    reference = ctx.artifact(value)
    ctx.write_json("outputs/snapshot.json", value)
    ctx.checkpoint("snapshot-source", source_key, {"snapshot_artifact": reference}, [reference])
    ctx.checkpoint("snapshot", key, {"snapshot_artifact": reference}, [reference])
    return {
        "complete": True,
        "snapshot_hash": snapshot.hash,
        "snapshot_path": "outputs/snapshot.json",
    }
