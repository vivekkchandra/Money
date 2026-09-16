"""Capability and durable-barrier regressions across real orchestration/storage."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest
from alembic import command
from alembic.config import Config

from money.flows.research import (
    DemoFirm,
    build_runtime,
    demo_snapshot,
    locked_reports,
    run_first_pass,
    run_research,
)
from money.policy.governance import consensus, evidence_independence
from money.schemas.contracts import (
    EvidenceRecord,
    JobStatus,
    LeanValidationReport,
    RedTeamReport,
    ResearchMandate,
    ResearchSnapshot,
    ResearchState,
    utc_now,
)
from money.storage.store import BarrierNotLocked, ResearchStore


@pytest.fixture
def durable_store(tmp_path, monkeypatch):
    database_url = f"sqlite:///{tmp_path / 'independence.db'}"
    monkeypatch.setenv("MONEY_ENV", "test")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")
    store = ResearchStore(database_url, allow_sqlite=True)
    yield store
    store.engine.dispose()


def claimed_job(store):
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("independence-test")
    assert claim is not None
    assert claim.job_id == job["id"]
    return job["id"], store.for_claim(claim)


def frozen_job(store):
    job_id, writer = claimed_job(store)
    instrument = build_runtime("demo").eligibility.get_instrument_metadata("DEMO.L")
    assert instrument is not None
    snapshot = demo_snapshot(instrument)
    writer.update_stage(job_id, JobStatus.SNAPSHOT_BUILD)
    writer.save_snapshot(job_id, snapshot)
    writer.update_stage(job_id, JobStatus.FIRST_PASS_RESEARCH)
    return job_id, writer, snapshot


def test_firm_workers_receive_only_independent_mandate_and_snapshot(durable_store):
    job_id, writer, snapshot = frozen_job(durable_store)
    meeting = Barrier(3, timeout=10)
    received = {}
    recorded = Lock()

    class InspectingFirm:
        def __init__(self, name):
            self.name = name

        def research(self, mandate, facts):
            assert type(mandate) is ResearchMandate
            assert type(facts) is ResearchSnapshot
            assert "reports" not in ResearchSnapshot.model_fields
            assert "peer_reports" not in ResearchSnapshot.model_fields
            assert not hasattr(facts, "get_reports")
            with recorded:
                received[self.name] = (mandate, facts)
            # All three must start before any result exists.
            meeting.wait()
            return DemoFirm(self.name).research(mandate, facts)

    firms = tuple(InspectingFirm(name) for name in ("tradingagents", "ai_hedge_fund", "qlib"))
    run_first_pass(job_id, writer, ResearchMandate(), snapshot, firms)
    assert len(received) == 3
    assert len({id(values[0]) for values in received.values()}) == 3
    assert len({id(values[1]) for values in received.values()}) == 3
    assert {values[1].hash for values in received.values()} == {snapshot.hash}
    assert {r.firm for r in locked_reports(job_id, durable_store)} == set(received)


def test_partial_reports_stay_sealed_until_all_are_persisted(durable_store, monkeypatch):
    job_id, writer, snapshot = frozen_job(durable_store)
    two_persisted, release_last = Event(), Event()
    saved = []
    lock = Lock()
    original_save = writer.save_report

    def observe_save(research_id, firm, report):
        original_save(research_id, firm, report)
        with lock:
            saved.append(firm)
            if len(saved) == 2:
                two_persisted.set()

    monkeypatch.setattr(writer, "save_report", observe_save)

    class LastFirm:
        def research(self, mandate, facts):
            if not release_last.wait(timeout=10):
                raise RuntimeError("test did not release the final firm")
            return DemoFirm("qlib").research(mandate, facts)

    firms = (DemoFirm("tradingagents"), DemoFirm("ai_hedge_fund"), LastFirm())
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(run_first_pass, job_id, writer, ResearchMandate(), snapshot, firms)
        try:
            assert two_persisted.wait(timeout=10)
            assert len(saved) == 2
            public = durable_store.public_reports(job_id)
            assert public["locked"] is False
            assert public["reports"]["tradingagents"] == {"status": "SEALED"}
            assert public["reports"]["ai_hedge_fund"] == {"status": "SEALED"}
            assert public["reports"]["qlib"] == {"status": "PENDING"}
            with pytest.raises(BarrierNotLocked):
                locked_reports(job_id, durable_store)
            with pytest.raises(BarrierNotLocked):
                writer.lock_first_pass(job_id)
            with pytest.raises(BarrierNotLocked):
                writer.update_stage(job_id, JobStatus.CREWAI_AUDIT)
            with pytest.raises(BarrierNotLocked):
                writer.save_artifact(job_id, "audit", {"completed": True})
        finally:
            release_last.set()
        future.result(timeout=10)

    # A new connection sees the persisted barrier and reports after request scope.
    reader = ResearchStore(durable_store.database_url, allow_sqlite=True)
    try:
        assert reader.get_job(job_id)["status"] == "FIRST_PASS_LOCKED"
        assert len(locked_reports(job_id, reader)) == 3
    finally:
        reader.engine.dispose()


def test_final_audit_observes_durable_lock_from_separate_connection(durable_store):
    job_id, writer = claimed_job(durable_store)
    runtime = build_runtime("demo")
    audit_called = []

    def audit(snapshot, reports, lean):
        reader = ResearchStore(durable_store.database_url, allow_sqlite=True)
        try:
            job = reader.get_job(job_id)
            assert job["locked_at"] is not None
            assert job["status"] == "CREWAI_AUDIT"
            assert {r.firm for r in locked_reports(job_id, reader)} == {r.firm for r in reports}
            audit_called.append(True)
        finally:
            reader.engine.dispose()
        return runtime.audit(snapshot, reports, lean)

    run_research(job_id, writer, replace(runtime, audit=audit))
    assert audit_called == [True]
    job = durable_store.get_job(job_id)
    assert job["status"] == "COMPLETE"
    assert job["packet"]["signal"] is None
    assert job["packet"]["runtime"] == "demo"


def test_one_failed_firm_prevents_validation_and_audit(durable_store):
    job_id, writer = claimed_job(durable_store)
    runtime = build_runtime("demo")

    class FailedFirm:
        def research(self, mandate, snapshot):
            raise RuntimeError("native firm failed")

    def forbidden(*args):
        pytest.fail("downstream evaluation started before the report barrier")

    runtime = replace(
        runtime, firms=(DemoFirm("tradingagents"), DemoFirm("ai_hedge_fund"), FailedFirm()),
        validate=forbidden, audit=forbidden, red_team=forbidden,
    )
    run_research(job_id, writer, runtime)
    job = durable_store.get_job(job_id)
    assert job["status"] == "FAILED"
    assert job["error_code"] == "FIRST_PASS_FAILED"
    assert job["locked_at"] is None
    assert job["packet"] is None
    with pytest.raises(BarrierNotLocked):
        durable_store.get_reports(job_id)


def test_syndicated_article_across_three_providers_remains_one_original_source():
    instrument = build_runtime("demo").eligibility.get_instrument_metadata("DEMO.L")
    assert instrument is not None
    original = demo_snapshot(instrument)
    article = original.evidence[-1]
    articles = tuple(EvidenceRecord(**(article.model_dump(exclude={"hash"}) | {
        "evidence_id": f"syndicated-{number}", "provider": f"provider-{number}",
        "source_id": f"provider-article-{number}",
        "canonical_source_id": "original-company-announcement-123",
    })) for number in range(3))
    snapshot = ResearchSnapshot(**(original.model_dump(exclude={"hash", "evidence"}) | {
        "evidence": (*original.evidence[:-1], *articles),
    }))
    reports = []
    for number, firm in enumerate(("tradingagents", "ai_hedge_fund", "qlib")):
        report = DemoFirm(firm).research(ResearchMandate(), snapshot)
        claim = report.claims[0].model_copy(update={"evidence_ids": (articles[number].evidence_id,)})
        reports.append(report.model_copy(update={"claims": (claim,)}))
    result = evidence_independence(snapshot, tuple(reports))
    assert result.unique_providers == 3
    assert result.unique_sources == 1
    assert result.source_overlap == pytest.approx(2 / 3)
    assert result.strength == "LOW"


@pytest.fixture
def consensus_inputs():
    runtime = build_runtime("demo")
    instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
    assert instrument is not None
    snapshot = demo_snapshot(instrument)
    mandate = ResearchMandate()
    reports = tuple(firm.research(mandate, snapshot).model_copy(update={"runtime": "live"})
                    for firm in runtime.firms)
    lean = LeanValidationReport(
        snapshot_id=snapshot.snapshot_id, state="PASS", runner_version="test-validation",
        observations=30, walk_forward=True, out_of_sample=True, pit_safe=True,
        survivorship_checked=True, costs_included=True, sensitivity_checked=True,
        spread_bps=10, slippage_bps=5,
    )
    audit = runtime.audit(snapshot, reports, lean)
    red = RedTeamReport(state="PASS", findings=())
    independence = evidence_independence(snapshot, reports)
    args = (mandate, snapshot, reports, lean, audit, red, independence, utc_now())
    assert consensus(*args)[0] == ResearchState.WATCH
    return args


def test_same_claim_identity_cannot_verify_three_firms(consensus_inputs):
    mandate, snapshot, reports, lean, audit, red, independence, now = consensus_inputs
    shared_claim = reports[0].claims[0]
    reports = tuple(report.model_copy(update={"claims": (shared_claim,)}) for report in reports)
    finding = next(f for f in audit.findings if f.claim_id == shared_claim.claim_id)
    audit = audit.model_copy(update={"findings": (finding,)})
    state, reasons = consensus(mandate, snapshot, reports, lean, audit, red, independence, now)
    assert state == ResearchState.INSUFFICIENT_EVIDENCE
    assert "DUPLICATE_CLAIM_IDENTITY" in reasons


@pytest.mark.parametrize("references", [("missing-source",), ("demo-news",)])
def test_audit_must_verify_claim_with_known_relevant_evidence(consensus_inputs, references):
    mandate, snapshot, reports, lean, audit, red, independence, now = consensus_inputs
    # All fixture claims cite prices or financials, so news is present but irrelevant.
    findings = tuple(f.model_copy(update={"evidence_ids": references}) for f in audit.findings)
    audit = audit.model_copy(update={"findings": findings})
    state, reasons = consensus(mandate, snapshot, reports, lean, audit, red, independence, now)
    assert state == ResearchState.INSUFFICIENT_EVIDENCE
    assert "AUDIT_EVIDENCE_LINK_INVALID" in reasons
    assert "CLAIM_VERIFICATION_INCOMPLETE" in reasons
