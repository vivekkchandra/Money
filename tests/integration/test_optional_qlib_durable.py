"""Synthetic durable queue tests, never qualification artifacts."""

from dataclasses import replace

import pytest
from pydantic import ValidationError

from money.flows.research import DemoFirm, build_runtime, run_first_pass, run_research
from money.research.replay import replay_decision
from money.schemas.contracts import DecisionPacket, ResearchMandate, ResearchSnapshot
from money.storage import BarrierNotLocked
from money.storage.store import StoreError


def prepared(store, enabled=False):
    runtime = build_runtime("demo")
    runtime = replace(
        runtime,
        qlib_enabled=enabled,
        firms=tuple(firm for firm in runtime.firms if enabled or firm.firm != "qlib"),
    )
    job = store.create_job("DEMO.L", ResearchMandate())
    owned = store.for_claim(store.claim_job("optional-qlib-test-worker"))
    original = runtime.snapshot_builder(runtime.eligibility.get_instrument_metadata("DEMO.L"))
    snapshot = ResearchSnapshot.model_validate(
        original.model_dump() | {"qlib_enabled": enabled, "hash": ""}
    )
    owned.update_stage(job["id"], "SNAPSHOT_BUILD")
    owned.save_snapshot(job["id"], snapshot)
    owned.update_stage(job["id"], "FIRST_PASS_RESEARCH")
    return job["id"], owned, runtime, snapshot


def test_two_required_reports_lock_only_when_explicitly_disabled(store):
    job_id, owned, runtime, snapshot = prepared(store)
    report = runtime.firms[0].research(ResearchMandate(), snapshot)
    owned.save_report(job_id, report.firm, report)
    with pytest.raises(BarrierNotLocked):
        owned.lock_first_pass(job_id)
    with pytest.raises(BarrierNotLocked):
        owned.get_reports(job_id)
    assert set(owned.public_reports(job_id)["reports"]) == snapshot.required_first_pass_firms
    report = runtime.firms[1].research(ResearchMandate(), snapshot)
    owned.save_report(job_id, report.firm, report)
    owned.lock_first_pass(job_id)
    assert set(owned.get_reports(job_id)) == {"tradingagents", "ai_hedge_fund"}


def test_disabled_snapshot_rejects_fabricated_qlib_report(store):
    job_id, owned, _, snapshot = prepared(store)
    report = DemoFirm("qlib").research(ResearchMandate(), snapshot)
    with pytest.raises(StoreError, match="disabled"):
        owned.save_report(job_id, "qlib", report)


def test_enabled_snapshot_does_not_lock_after_two_reports(store):
    job_id, owned, runtime, snapshot = prepared(store, enabled=True)
    for firm in runtime.firms[:2]:
        report = firm.research(ResearchMandate(), snapshot)
        owned.save_report(job_id, report.firm, report)
    with pytest.raises(BarrierNotLocked):
        owned.lock_first_pass(job_id)
    with pytest.raises(ValueError, match="configured"):
        run_first_pass(job_id, owned, ResearchMandate(), snapshot, runtime.firms[:2])


def test_resume_cannot_change_frozen_qlib_policy(store):
    job_id, owned, _, _ = prepared(store, enabled=True)
    runtime = build_runtime("demo")
    runtime = replace(runtime, qlib_enabled=False, firms=runtime.firms[:2])
    with pytest.raises(ValueError, match="RESEARCH_QLIB_POLICY_CHANGED"):
        run_research(job_id, owned, runtime)
    assert owned.get_checkpoint(job_id)["sealed_firms"] == []


def test_disabled_flow_persists_two_reports_lean_and_replayable_provenance(store):
    runtime = build_runtime("demo")
    runtime = replace(
        runtime,
        qlib_enabled=False,
        firms=tuple(firm for firm in runtime.firms if firm.firm != "qlib"),
    )
    job = store.create_job("DEMO.L", ResearchMandate())
    owned = store.for_claim(store.claim_job("optional-qlib-flow-worker"))
    run_research(job["id"], owned, runtime)
    complete = store.get_job(job["id"])
    assert complete["status"] == "COMPLETE"
    packet = DecisionPacket.model_validate(complete["packet"])
    assert packet.qlib_enabled is False
    assert packet.frozen_snapshot.qlib_enabled is False
    assert {report.firm for report in packet.reports} == {"tradingagents", "ai_hedge_fund"}
    assert packet.lean.state == "INSUFFICIENT_EVIDENCE"
    assert packet.signal is None
    assert "LEAN_INSUFFICIENT_EVIDENCE" in packet.reasons
    replay = replay_decision(store, job["id"], money_version="test", git_sha="test")
    assert replay["changed"] is False
    with pytest.raises(ValidationError):
        DecisionPacket.model_validate(packet.model_dump() | {"qlib_enabled": True, "hash": ""})
    with pytest.raises(ValidationError):
        DecisionPacket.model_validate(packet.model_dump() | {"frozen_snapshot": None, "hash": ""})
