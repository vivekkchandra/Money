"""Fixture-only flow checks; no live provider/firm qualification is asserted."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from money.data.quality.market import evaluate_market_quality
from money.flows.research import build_runtime, run_research
from money.research.replay import replay_decision
from money.risk.costs import CostApplicability
from money.schemas.contracts import (
    Candidate,
    EvidenceRecord,
    PriceBar,
    ResearchMandate,
    ResearchSnapshot,
    content_hash,
    utc_now,
)
from money.signals.generation import SignalDesign, SignalPolicy, generate_signal
from money.storage import ResearchStore
from money.storage.store import StoreError
from money.worker import run_once


def qualified_fixture(*, rich_prices=False, unavailable_code=None):
    base = build_runtime("demo")

    class FirmFixture:
        def __init__(self, firm):
            self.firm, self.delegate = firm.firm, firm

        def research(self, mandate, snapshot):
            return self.delegate.research(mandate, snapshot).model_copy(update={"runtime": "live"})

    def snapshot_builder(instrument):
        snapshot = base.snapshot_builder(instrument)
        if not rich_prices:
            return snapshot
        reference = next(e for e in snapshot.evidence if isinstance(e.payload, PriceBar))
        records = []
        for index in range(60):
            observed = snapshot.price_cutoff - timedelta(days=59 - index)
            data = reference.model_dump()
            data.update(
                evidence_id=f"price-{index}",
                observation_time=observed,
                publication_time=observed,
                hash="",
                payload=PriceBar(
                    open=99 + index,
                    high=105 + index,
                    low=95 + index,
                    close=100 + index,
                    currency="GBX",
                    volume=1_000_000,
                ),
            )
            records.append(EvidenceRecord.model_validate(data))
        data = snapshot.model_dump()
        data.update(
            hash="",
            evidence=tuple(records)
            + tuple(e for e in snapshot.evidence if not isinstance(e.payload, PriceBar)),
        )
        return ResearchSnapshot.model_validate(data)

    def validate(snapshot, reports):
        return base.validate(snapshot, reports).model_copy(
            update={
                "state": "PASS",
                "observations": 60,
                "walk_forward": True,
                "out_of_sample": True,
                "pit_safe": True,
                "survivorship_checked": True,
                "costs_included": True,
                "sensitivity_checked": True,
                "spread_bps": 20.0,
                "slippage_bps": 10.0,
                "scenario_policy_hash": content_hash(SignalPolicy()),
            }
        )

    def builder(job_id, mandate, snapshot, reports, lean, audit, red_team, state):
        if unavailable_code:
            return SignalDesign(policy_version="test", reasons=(unavailable_code,))
        now = utc_now()
        quality = evaluate_market_quality(
            snapshot,
            now,
            spread_bps=Decimal(20),
            adjustment_basis="RAW",
            corporate_actions_complete=True,
        )
        costs = CostApplicability(
            sdrt="APPLIES",
            evidence_source="test-only",
            broker_round_trip_fee_gbp=0,
            broker_fee_source="test-only",
            other_round_trip_charges_gbp=0,
            other_charge_source="test-only",
        )
        return generate_signal(
            job_id,
            mandate,
            snapshot,
            reports,
            lean,
            audit,
            red_team,
            state=state,
            issued_at=now,
            market_quality=quality,
            cost_applicability=costs,
        )

    return replace(
        base,
        mode="live",
        firms=tuple(FirmFixture(firm) for firm in base.firms),
        snapshot_builder=snapshot_builder,
        validate=validate,
        signal_builder=builder,
    )


def test_missing_scenario_evidence_downgrades_to_persisted_insufficient(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    run_once(
        store,
        qualified_fixture(unavailable_code="COST_COVERAGE_MISSING"),
        worker_id="fixture",
        mode="live",
    )
    result = store.get_job(job["id"])
    assert result["status"] == "COMPLETE", result
    assert result["final_state"] == "INSUFFICIENT_EVIDENCE"
    assert result["packet"]["reasons"] == ["COST_COVERAGE_MISSING"]
    assert result["packet"]["signal"] is None
    replay = replay_decision(store, job["id"], money_version="test", git_sha="unknown")
    assert replay["changed"] is False


def test_unknown_downgrade_reason_cannot_publish_packet(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    run_once(
        store,
        qualified_fixture(unavailable_code="UNTRUSTED_INSTRUCTION"),
        worker_id="fixture",
        mode="live",
    )
    result = store.get_job(job["id"])
    assert result["status"] == "FAILED"
    assert result["packet"] is None


def test_positive_signal_requires_exact_recomputed_sealed_design(store: ResearchStore, monkeypatch):
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("fixture")
    owned = store.for_claim(claim)
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(owned, "complete_job", lambda job_id, packet: captured.append(packet))
        run_research(job["id"], owned, qualified_fixture(rich_prices=True))
    packet = captured[0]
    assert packet.signal is not None
    assert packet.cross_examination_hash is not None
    for field, value in (("cross_examination_hash", "f" * 64), ("cross_examination_rounds", 2)):
        forged_cross = packet.model_dump(mode="json")
        forged_cross.update({"hash": "", field: value})
        with pytest.raises(StoreError, match="[Cc]ross-examination"):
            owned.complete_job(job["id"], forged_cross)
    forged = packet.model_dump(mode="json")
    forged["hash"] = ""
    forged["signal"]["potential_targets"] = ["99999"]
    with pytest.raises(StoreError, match="sealed design"):
        owned.complete_job(job["id"], forged)
    owned.complete_job(job["id"], packet)
    result = store.get_job(job["id"])
    assert result["status"] == "COMPLETE"
    assert result["packet"]["signal"] == packet.signal.model_dump(mode="json")
    replay = replay_decision(store, job["id"], money_version="test", git_sha="unknown")
    assert replay["changed"] is False


def test_zero_discovery_rejects_before_any_research_firm_runs(store: ResearchStore, monkeypatch):
    runtime = qualified_fixture()

    def forbidden(*args):
        raise AssertionError("first-pass research must not run")

    monkeypatch.setattr(
        "money.flows.research.discover_snapshot",
        lambda snapshot: Candidate(ticker=snapshot.ticker, discovery=()),
    )
    for firm in runtime.firms:
        monkeypatch.setattr(firm, "research", forbidden)
    job = store.create_job("DEMO.L", ResearchMandate())
    run_once(store, runtime, worker_id="fixture", mode="live")
    result = store.get_job(job["id"])
    assert result["status"] == "REJECTED", result
    assert result["packet"]["final_state"] == "REJECT"
    assert result["packet"]["reasons"] == ["NO_DISCOVERY_EVIDENCE"]
