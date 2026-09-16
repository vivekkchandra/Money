"""Fenced insert-only personal evidence studies on the existing durable queue."""

from uuid import uuid4

from sqlalchemy import select

from money.research.rnd_contracts import RndResult, RndSnapshot
from money.storage import ResearchStore
from money.storage import models as db
from money.storage.store import StoreError, digest, now_utc


def save_snapshot(store: ResearchStore, job_id: str, snapshot: RndSnapshot) -> None:
    # Revalidate, including frozen hashes, even if a caller used model_construct/copy.
    checked = RndSnapshot.model_validate_json(snapshot.model_dump_json())
    with store.transaction() as connection:
        row = store._owned_job(connection, job_id)
        if (
            row["research_kind"] != "live_rnd" or row["status"] != "SNAPSHOT_BUILD"
            or row["snapshot_id"] is not None or row["ticker"] != checked.ticker
        ):
            raise StoreError("RND_SNAPSHOT_BOUNDARY")
        connection.execute(db.snapshots.insert().values(
            id=checked.snapshot_id, job_id=job_id, payload=checked.model_dump(mode="json"),
            created_at=now_utc(),
        ))
        for evidence in checked.evidence:
            connection.execute(db.evidence.insert().values(
                id=f"{job_id}:{evidence.evidence_id}", job_id=job_id,
                payload=evidence.model_dump(mode="json"), created_at=now_utc(),
            ))
        connection.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(
            snapshot_id=checked.snapshot_id,
        ))
        store._audit(connection, job_id, "RND_SNAPSHOT_FROZEN", {"hash": checked.hash})


def complete_study(store: ResearchStore, job_id: str, result: RndResult) -> None:
    checked = RndResult.model_validate_json(result.model_dump_json())
    with store.transaction() as connection:
        row = store._owned_job(connection, job_id)
        if (
            row["research_kind"] != "live_rnd" or row["status"] != "SNAPSHOT_BUILD"
            or checked.research_id != job_id or checked.ticker != row["ticker"]
        ):
            raise StoreError("RND_RESULT_BOUNDARY")
        frozen = connection.scalar(select(db.snapshots.c.payload).where(
            db.snapshots.c.job_id == job_id,
        ))
        snapshot = RndSnapshot.model_validate(frozen)
        if (
            checked.snapshot_id != snapshot.snapshot_id or checked.snapshot_hash != snapshot.hash
            or checked.issued_at < snapshot.created_at
        ):
            raise StoreError("RND_RESULT_SNAPSHOT_MISMATCH")
        from money.research.rnd import study_analysis

        if checked.analysis != study_analysis(snapshot.market):
            raise StoreError("RND_RESULT_ANALYSIS_MISMATCH")
        # This path cannot mark any independent firm as executed or issue a signal.
        native = {"tradingagents", "ai_hedge_fund", "qlib", "lean", "crewai"}
        if {c.component for c in checked.components} & native != native or any(
            c.component in native and c.status == "READY" for c in checked.components
        ):
            raise StoreError("RND_NATIVE_QUALIFICATION_REQUIRED")
        expected = {"yfinance": "READY"}
        expected.update({item["provider"]: item["status"] for item in (
            *snapshot.official, *snapshot.macro,
        )})
        actual = {c.component: c.status for c in checked.components}
        if (
            len(actual) != len(checked.components)
            or any(actual.get(name) != status for name, status in expected.items())
            or set(actual) != set(expected) | native
        ):
            raise StoreError("RND_COMPONENT_STATUS_MISMATCH")
        data = checked.model_dump(mode="json")
        connection.execute(db.packets.insert().values(
            job_id=job_id, payload=data, content_hash=digest(data), created_at=now_utc(),
        ))
        connection.execute(db.jobs.update().where(db.jobs.c.id == job_id).values(
            status="COMPLETE", current_stage="COMPLETE", completed_at=now_utc(),
            updated_at=now_utc(), lease_token=None, lease_until=None,
        ))
        store._audit(connection, job_id, "RND_STUDY_PERSISTED", {
            "final_state": checked.final_state, "native_research_complete": False,
        })
        from money.storage.production_models import alert_outbox

        connection.execute(alert_outbox.insert().values(
            id=str(uuid4()), job_id=job_id, workspace_id=row["workspace_id"], channel="web",
            state="PENDING", attempts=0, created_at=now_utc(),
            payload={"event": "RND_EVIDENCE_COLLECTED", "research_id": job_id,
                     "message": "Personal R&D evidence study saved; native research incomplete."},
        ))
