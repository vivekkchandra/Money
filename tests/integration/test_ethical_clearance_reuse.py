"""Synthetic integration mechanics, never issuer or native-runtime evidence."""

from dataclasses import replace
from datetime import timedelta

import pytest

from money.adapters.eligibility import Trading212EligibilityAdapter
from money.crews.cross_examination import run_cross_examination
from money.flows.research import build_runtime, run_research
from money.research.ethics import ethical_policy_hash
from money.schemas.contracts import (
    EXCLUDED_ACTIVITIES,
    EthicalClearance,
    ResearchMandate,
    ResearchSnapshot,
    content_hash,
    utc_now,
)


def clearance(*, result="PASS", evidence="original synthetic document"):
    now = utc_now()
    fields = {
        "result": result,
        "issuer_key": "synthetic-demo-issuer",
        "isins": ("GB0006389398",),
        "screened_at": now - timedelta(days=10),
        "valid_until": now + timedelta(days=20),
        "policy_hash": ethical_policy_hash(),
        "evidence_hashes": (content_hash({"synthetic": evidence}),),
        "assessed_exclusions": EXCLUDED_ACTIVITIES,
        "reasons": (),
        "business_activities": ("software",),
    }
    unsigned = EthicalClearance.model_construct(**fields, screening_hash="")
    digest = content_hash(unsigned.model_dump(mode="json", exclude={"screening_hash"}))
    return EthicalClearance.model_validate(fields | {"screening_hash": digest})


def prepare(store):
    original = build_runtime("demo")
    instrument = original.eligibility.get_instrument_metadata("DEMO.L").model_copy(
        update={"ethical_clearance": clearance()},
    )
    current = [instrument]
    authority = Trading212EligibilityAdapter((instrument,))
    authority.get_instrument_metadata = lambda ticker: current[0]
    runtime = replace(
        original, eligibility=authority, qlib_enabled=False,
        firms=tuple(firm for firm in original.firms if firm.firm != "qlib"),
    )
    job = store.create_job("DEMO.L", ResearchMandate())
    owned = store.for_claim(store.claim_job("ethics-test-worker"))
    return job["id"], owned, runtime, current


def test_one_pass_reaches_both_firms_mandatory_lean_cio_without_rescreen(store, monkeypatch):
    job_id, owned, runtime, current = prepare(store)
    seen = []
    original_lean, original_audit = runtime.validate, runtime.audit

    def no_new_screen(*args, **kwargs):
        raise AssertionError("Downstream stages must reuse the frozen issuer screening")

    monkeypatch.setattr("money.research.ethics.screen_issuer", no_new_screen)

    def lean(snapshot, reports):
        seen.append(("lean", snapshot.instrument.ethical_clearance))
        assert {report.firm for report in reports} == {"tradingagents", "ai_hedge_fund"}
        assert set(owned.get_reports(job_id)) == {"tradingagents", "ai_hedge_fund"}
        return original_lean(snapshot, reports)

    def audit(snapshot, reports, validation):
        seen.append(("cio", snapshot.instrument.ethical_clearance))
        return original_audit(snapshot, reports, validation)

    run_research(job_id, owned, replace(runtime, validate=lean, audit=audit))
    result = store.get_job(job_id)
    assert result["status"] == "COMPLETE"
    assert seen == [("lean", current[0].ethical_clearance), ("cio", current[0].ethical_clearance)]
    assert result["packet"]["frozen_snapshot"]["instrument"]["ethical_clearance"]["result"] == "PASS"
    assert result["packet"]["qlib_enabled"] is False
    assert result["packet"]["lean"]["state"] == "INSUFFICIENT_EVIDENCE"
    assert result["packet"]["signal"] is None


@pytest.mark.parametrize("change", ["revoked", "material_evidence"])
def test_resume_rechecks_current_clearance_without_mutating_frozen_snapshot(store, change):
    job_id, owned, runtime, current = prepare(store)
    instrument = current[0]
    original = runtime.snapshot_builder(instrument)
    snapshot = ResearchSnapshot.model_validate(
        original.model_dump() | {"qlib_enabled": False, "hash": ""},
    )
    owned.save_artifact(job_id, "eligibility", instrument)
    owned.update_stage(job_id, "SNAPSHOT_BUILD")
    owned.save_snapshot(job_id, snapshot)
    current[0] = instrument.model_copy(update={
        "ethical_clearance": clearance(
            result="UNKNOWN" if change == "revoked" else "PASS", evidence="changed document",
        ),
    })
    with pytest.raises(ValueError, match="ETHICAL_CLEARANCE_.*"):
        run_research(job_id, owned, runtime)
    checkpoint = owned.get_checkpoint(job_id)
    assert checkpoint["snapshot"]["hash"] == snapshot.hash
    assert checkpoint["sealed_firms"] == []
    assert "lean" not in checkpoint["artifacts"]


def test_contradiction_discovered_during_lean_blocks_cio_and_publication(store):
    job_id, owned, runtime, current = prepare(store)
    original_validate = runtime.validate

    def lean(snapshot, reports):
        current[0] = current[0].model_copy(update={"ethical_clearance": clearance(result="FAIL")})
        return original_validate(snapshot, reports)

    def forbidden_audit(*args):
        raise AssertionError("Revocation must prevent the CIO from starting")

    with pytest.raises(ValueError, match="ETHICAL_CLEARANCE_NO_LONGER_CURRENT"):
        run_research(job_id, owned, replace(runtime, validate=lean, audit=forbidden_audit))
    checkpoint = owned.get_checkpoint(job_id)
    assert "lean" in checkpoint["artifacts"]
    assert "audit" not in checkpoint["artifacts"]
    assert store.get_job(job_id)["packet"] is None


def test_revocation_before_publication_cannot_publish_the_old_pass(store):
    job_id, owned, runtime, current = prepare(store)

    def examination(job_id, mandate, snapshot, reports, lean, audit):
        result = run_cross_examination(snapshot, reports, lean, audit, first_pass_locked=True)
        current[0] = current[0].model_copy(update={
            "ethical_clearance": clearance(result="UNKNOWN"),
        })
        return result

    with pytest.raises(ValueError, match="ETHICAL_CLEARANCE_NO_LONGER_CURRENT"):
        run_research(job_id, owned, replace(runtime, cross_examine=examination))
    assert "cross_examination" in owned.get_checkpoint(job_id)["artifacts"]
    assert store.get_job(job_id)["packet"] is None
