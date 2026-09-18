"""Personal research snapshots: live membership, optional facts, no release grant."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from uuid import uuid4

from money.qualification.core import QualificationContext
from money.schemas.contracts import (
    DocumentFact,
    EvidenceRecord,
    FinancialFact,
    InstrumentMetadata,
    ResearchEnrichment,
    ResearchSnapshot,
)


def freeze_research_universe(
    ctx: QualificationContext, master: dict[str, Any]
) -> tuple[list[dict[str, Any]], tuple[str, str]]:
    """Verify raw membership first and freeze ALL admissions before selection."""
    from money.qualification.universe_admission import current_research_rows

    rows = current_research_rows(ctx, master)
    members = [
        {key: row.get(key) for key in (
            "trading212_id", "isin", "name", "quote_currency", "instrument_type",
            "instrument_row_sha256", "observed_at", "valid_until",
        )}
        for row in sorted(rows, key=lambda item: item["trading212_id"])
    ]
    ref = ctx.artifact({
        "purpose": "RESEARCH_TESTING",
        "universe_policy_version": master["universe_policy_version"],
        "source_provenance": master["universe_provenance"],
        "frozen_at": ctx.now.isoformat(),
        "members": members,
        "production_qualified": False,
    })
    ctx.write_json("outputs/research-universe-snapshot.json", {
        "artifact": ref, "member_count": len(members), "production_qualified": False,
    })
    return rows, ref


def _optional_records(
    ctx: QualificationContext, row: dict[str, Any], snapshot_id: str,
) -> tuple[tuple[EvidenceRecord, ...], tuple[str, ...]]:
    """Use actual cached provider bytes; corrupt/missing enrichment is omitted.

    Original availability times are never moved. A failed optional observation
    is not a reason to substitute facts or to stop the broker-only snapshot.
    """
    from money.data.identifiers import InstrumentIdentifiers
    from money.data.provider_probes import ProviderProbeReport

    records: list[EvidenceRecord] = []
    warnings: list[str] = []
    reports = row.get("provider_reports") or {}
    if not isinstance(reports, dict):
        return (), ("OPTIONAL_PROVIDER_REPORTS_MALFORMED",)
    for provider, value in reports.items():
        try:
            identity = InstrumentIdentifiers.model_validate(row.get("identifiers"))
            identity.require_current(ctx.now)
            if identity.trading212_id != row["trading212_id"] or identity.isin != row.get("isin"):
                raise ValueError("IDENTITY_MISMATCH")
            ref = value["report_ref"]
            report = ProviderProbeReport.model_validate_json(ctx.verify_artifact(ref["sha256"], ref["path"]))
            if report.provider != provider or len(report.samples) != 1:
                raise ValueError("REPORT_IDENTITY_MISMATCH")
            original = report.samples[0]
            if any(getattr(original, key) != getattr(identity, key) for key in (
                "trading212_id", "isin", "quote_currency", "provider_symbols",
            )):
                raise ValueError("REPORT_IDENTITY_MISMATCH")
            for dataset in report.datasets:
                if dataset.dataset == "ohlcv" or dataset.status != "RETRIEVED":
                    continue
                if not dataset.artifact_hash or not dataset.artifact_path:
                    raise ValueError("DATASET_BYTES_MISSING")
                raw = json.loads(ctx.verify_artifact(
                    dataset.artifact_hash, "artifacts/" + dataset.artifact_path,
                ))
                if raw.get("provider") != provider or raw.get("ticker") != identity.ticker:
                    raise ValueError("DATASET_IDENTITY_MISMATCH")
                if raw.get("dataset") != dataset.dataset or len(raw.get("records", [])) != dataset.record_count:
                    raise ValueError("DATASET_RECORD_COUNT_MISMATCH")
                for item in raw["records"]:
                    record = EvidenceRecord.model_validate(item)
                    if record.payload.kind != dataset.dataset or record.conflicting:
                        raise ValueError("DATASET_KIND_CONFLICT")
                    if (
                        not record.retrieval_time <= ctx.now < record.fresh_until
                        or record.observation_time > ctx.now
                        or (isinstance(record.payload, FinancialFact) and record.payload.period_end > ctx.now)
                    ):
                        warnings.append(provider + ":" + dataset.dataset + ":STALE")
                        continue
                    records.append(EvidenceRecord.model_validate({
                        **record.model_dump(), "snapshot_id": snapshot_id, "hash": "",
                    }))
        except (ValueError, KeyError, TypeError, OSError):
            warnings.append(provider + ":OPTIONAL_EVIDENCE_UNAVAILABLE_OR_INVALID")
    counts: dict[str, int] = {}
    for record in records:
        counts[record.evidence_id] = counts.get(record.evidence_id, 0) + 1
    if any(count > 1 for count in counts.values()):
        warnings.append("OPTIONAL_DUPLICATE_EVIDENCE_OMITTED")
    return tuple(r for r in records if counts[r.evidence_id] == 1), tuple(sorted(set(warnings)))


def build_testing_snapshot(
    ctx: QualificationContext, row: dict[str, Any], universe_ref: tuple[str, str],
    market_records: tuple[EvidenceRecord, ...] = (),
) -> ResearchSnapshot:
    """A data-poor snapshot is honest research input, not backtest-ready data."""
    frozen = json.loads(ctx.verify_artifact(*universe_ref))
    member = next((m for m in frozen["members"] if m["trading212_id"] == row["trading212_id"]), None)
    if member is None or any(member.get(key) != row.get(key) for key in member):
        raise ValueError("RESEARCH_MEMBER_NOT_IN_FROZEN_UNIVERSE")
    if frozen.get("purpose") != "RESEARCH_TESTING":
        raise ValueError("RESEARCH_UNIVERSE_PURPOSE_MISMATCH")
    observed, until = datetime.fromisoformat(row["observed_at"]), datetime.fromisoformat(row["valid_until"])
    if not observed <= ctx.now < until:
        raise ValueError("RESEARCH_MEMBERSHIP_EXPIRED")
    snapshot_id = str(uuid4())
    # This is an opaque Money research key, not an exchange/provider ticker.
    # Raw broker ID remains in the evidence; no suffix or security map is guessed.
    ticker = "T212_" + hashlib.sha256(row["trading212_id"].encode()).hexdigest()[:24].upper()
    broker = EvidenceRecord(
        snapshot_id=snapshot_id,
        evidence_id="t212:" + row["instrument_row_sha256"],
        source="Trading 212 live accessible instrument metadata",
        provider="trading212", source_id=row["trading212_id"],
        canonical_source_id="trading212:" + row["trading212_id"],
        observation_time=observed, publication_time=None, retrieval_time=observed,
        fresh_until=until, pit_safe=False, critical=True,
        payload=DocumentFact(
            kind="instrument_metadata", title="Observed live instrument identity",
            excerpt=json.dumps(member, sort_keys=True),
            url="https://live.trading212.com/api/v0/equity/metadata/instruments",
        ),
    )
    optional, warnings = _optional_records(ctx, row, snapshot_id)
    market = tuple(EvidenceRecord.model_validate({
        **record.model_dump(), "snapshot_id": snapshot_id, "hash": "",
    }) for record in market_records)
    records = (broker, *market, *optional)
    kinds = {record.payload.kind for record in records}
    missing = tuple(sorted({
        *("NO_" + kind.upper() + "_EVIDENCE" for kind in ("ohlcv", "financial", "filing", "news", "corporate_action") if kind not in kinds),
        "ETHICAL_ANNOTATION_IS_NOT_RESEARCH_ADMISSION_OR_TRADE_APPROVAL",
        *warnings,
    }))
    enrichment = tuple(
        ResearchEnrichment(
            source=kind,
            status="AVAILABLE" if kind in kinds else "UNAVAILABLE",
            reason="Frozen verified source bytes" if kind in kinds else "No current admissible evidence in snapshot",
            evidence_ids=tuple(item.evidence_id for item in records if item.payload.kind == kind),
        )
        for kind in ("ohlcv", "financial", "filing", "news", "corporate_action")
    )
    return ResearchSnapshot(
        purpose="RESEARCH_TESTING", usage_mode="PERSONAL_RESEARCH", qlib_enabled=False,
        universe_hash=universe_ref[0], snapshot_id=snapshot_id, ticker=ticker,
        created_at=ctx.now, price_cutoff=ctx.now, news_cutoff=ctx.now,
        filing_cutoff=ctx.now, fundamental_cutoff=ctx.now,
        instrument=InstrumentMetadata(
            ticker=ticker, company=row.get("name") or row["trading212_id"],
            instrument_type="STOCK", quote_currency=row["quote_currency"],
            currently_available=True, activities_verified=False,
            verified_at=observed, source="Trading 212 live metadata", provider="trading212",
            source_id=row["trading212_id"],
        ),
        evidence=records, enrichment=enrichment, missing_data=missing,
    )
