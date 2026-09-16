"""Re-evaluate deterministic gates using original evidence and decision time."""

from uuid import uuid4

from sqlalchemy import select

from money.crews.cross_examination import CrossExaminationPacket
from money.policy.governance import consensus, evidence_independence
from money.schemas.contracts import DecisionPacket, ResearchSnapshot, ResearchState, content_hash
from money.signals.generation import DOWNGRADE_REASONS, SignalDesign, generate_signal
from money.storage import ResearchStore
from money.storage import models as db
from money.storage.production_models import replay_runs
from money.storage.store import StoreError, digest, now_utc


def replay_decision(store: ResearchStore, job_id: str, *, money_version: str, git_sha: str) -> dict:
    job = store.get_job(job_id)
    if job is None or job["status"] != "COMPLETE":
        raise StoreError("Replay requires an accessible completed decision packet")
    with store.engine.connect() as connection:
        original = (
            connection.execute(select(db.packets).where(db.packets.c.job_id == job_id))
            .mappings()
            .one()
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
        cross_data = connection.scalar(
            select(db.artifacts.c.payload).where(
                db.artifacts.c.job_id == job_id,
                db.artifacts.c.kind == "cross_examination",
            )
        )
    if digest(original["payload"]) != original["content_hash"]:
        raise StoreError("Original decision packet integrity failed")
    packet = DecisionPacket.model_validate(original["payload"])
    snapshot = ResearchSnapshot.model_validate(frozen)
    if snapshot.hash != packet.snapshot_hash:
        raise StoreError("Original snapshot integrity failed")
    cross_disagreement = False
    if cross_data is not None:
        cross = CrossExaminationPacket.model_validate(cross_data)
        if (
            cross.hash != packet.cross_examination_hash
            or len(cross.rounds) != packet.cross_examination_rounds
            or cross.snapshot_id != snapshot.snapshot_id
            or cross.snapshot_hash != snapshot.hash
            or len(cross.report_hashes) != 3
            or dict(cross.report_hashes)
            != {report.firm: content_hash(report) for report in packet.reports}
            or cross.lean_hash != content_hash(packet.lean)
            or cross.initial_audit_hash != content_hash(packet.audit)
            or not snapshot.created_at <= cross.issued_at <= packet.issued_at
        ):
            raise StoreError("Original cross-examination integrity failed")
        cross_disagreement = cross.material_disagreement
    elif packet.cross_examination_hash or packet.cross_examination_rounds:
        raise StoreError("Original cross-examination artifact is missing")
    independence = evidence_independence(snapshot, packet.reports)
    state, reasons = consensus(
        packet.mandate,
        snapshot,
        packet.reports,
        packet.lean,
        packet.audit,
        packet.red_team,
        independence,
        packet.issued_at,
        rounds=packet.cross_examination_rounds,
        cross_examination_disagreement=cross_disagreement,
    )
    signal_changed = False
    if design_data is not None:
        sealed_design = SignalDesign.model_validate(design_data)
        reproduced = sealed_design
        if (
            sealed_design.policy is not None
            and sealed_design.market_quality is not None
            and sealed_design.cost_applicability is not None
            and sealed_design.designed_at is not None
        ):
            reproduced = generate_signal(
                job_id,
                packet.mandate,
                snapshot,
                packet.reports,
                packet.lean,
                packet.audit,
                packet.red_team,
                state=state,
                issued_at=sealed_design.designed_at,
                market_quality=sealed_design.market_quality,
                cost_applicability=sealed_design.cost_applicability,
                policy=sealed_design.policy,
                rounds=packet.cross_examination_rounds,
            )
        signal_changed = reproduced != sealed_design
        if (
            state in {ResearchState.WATCH, ResearchState.RESEARCH_CANDIDATE}
            and reproduced.signal is None
        ):
            if not reproduced.reasons or not set(reproduced.reasons) <= DOWNGRADE_REASONS:
                raise StoreError(
                    "Replay signal downgrade is not a recognized deterministic failure"
                )
            state, reasons = ResearchState.INSUFFICIENT_EVIDENCE, reproduced.reasons
    result = {
        "replay_id": str(uuid4()),
        "research_id": job_id,
        "original_hash": original["content_hash"],
        "snapshot_hash": snapshot.hash,
        "decision_timestamp": original["payload"]["issued_at"],
        "money_version": money_version,
        "git_sha": git_sha,
        "original_state": packet.final_state.value,
        "replayed_state": state.value,
        "reasons": list(reasons),
        "changed": state != packet.final_state or reasons != packet.reasons or signal_changed,
        "signal_design_changed": signal_changed,
        "scope": "DETERMINISTIC_GATES_ONLY",
        "independence": independence.model_dump(mode="json"),
    }
    with store.transaction() as connection:
        connection.execute(
            replay_runs.insert().values(
                id=result["replay_id"],
                job_id=job_id,
                original_hash=original["content_hash"],
                payload=result,
                created_at=now_utc(),
            )
        )
    return result
