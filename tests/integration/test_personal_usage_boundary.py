"""Synthetic boundary tests; no fixture represents real provider permission."""

from dataclasses import replace

import pytest

from money.flows.research import build_runtime, run_research
from money.schemas.contracts import ResearchMandate, ResearchSnapshot
from money.storage.store import StoreError


def test_personal_two_firms_and_mandatory_lean_do_not_publish_or_export_raw_sources(store):
    original = build_runtime("demo")
    stages = []

    def snapshot_builder(instrument):
        snapshot = original.snapshot_builder(instrument)
        return ResearchSnapshot.model_validate({
            **snapshot.model_dump(), "usage_mode": "PERSONAL_RESEARCH",
            "qlib_enabled": False, "hash": "",
        })

    def lean(snapshot, reports):
        assert snapshot.usage_mode == "PERSONAL_RESEARCH"
        assert {report.firm for report in reports} == {"tradingagents", "ai_hedge_fund"}
        stages.append("lean")
        return original.validate(snapshot, reports)

    def cio(snapshot, reports, validation):
        assert stages == ["lean"]
        stages.append("cio")
        return original.audit(snapshot, reports, validation)

    runtime = replace(
        original, snapshot_builder=snapshot_builder, validate=lean, audit=cio,
        qlib_enabled=False, firms=tuple(firm for firm in original.firms if firm.firm != "qlib"),
    )
    job = store.create_job("DEMO.L", ResearchMandate())
    owned = store.for_claim(store.claim_job("personal-usage-test-worker"))
    with pytest.raises(StoreError, match="Personal research cannot publish"):
        run_research(job["id"], owned, runtime)
    assert stages == ["lean", "cio"]
    assert store.get_job(job["id"])["packet"] is None
    assert set(owned.get_reports(job["id"])) == {"tradingagents", "ai_hedge_fund"}
    with pytest.raises(StoreError, match="cannot redistribute"):
        store.get_evidence(job["id"])
    with pytest.raises(StoreError, match="cannot export reports"):
        store.public_reports(job["id"])
