import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DatabaseError

from money.adapters.native_qlib import LinearModelArtifact, QualifiedLinearModel
from money.alerts.delivery import AlertOutbox
from money.data.resilience import ProviderCircuit
from money.data.security import ProviderFailure
from money.flows.research import build_runtime
from money.models.registry import ModelRegistry
from money.offline_research.promotion import (
    IndependentApproval,
    OfflineValidationEvidence,
    PromotionEvidence,
)
from money.performance.outcomes import OutcomeBar
from money.research.budgets import BudgetLimits, TokenBudgetManager
from money.research.outcomes import evaluate_signal_outcome
from money.research.replay import replay_decision
from money.schemas.contracts import ResearchMandate, ResearchSignal, ResearchSnapshot, utc_now
from money.storage import ResearchStore
from money.storage import models as db
from money.storage.production_models import (
    alert_outbox,
    model_registry,
    provider_state,
    replay_runs,
)
from money.storage.store import StoreError
from money.worker import run_once


def model_fixture():
    now = utc_now()
    artifact = LinearModelArtifact(
        model_id="baseline",
        model_version="v1",
        coefficients=(1, 2, 3, 4, 5),
        intercept=0,
        training_data_version="fixture-dataset",
        dataset_hash="a" * 64,
        training_start=now - timedelta(days=100),
        training_cutoff=now - timedelta(days=60),
        validation_start=now - timedelta(days=59),
        validation_end=now - timedelta(days=10),
        oos_observations=40,
        walk_forward_folds=4,
        oos_rmse=0.03,
        pit_validated=True,
    )
    reports = {}
    validations = []
    for kind in ("POINT_IN_TIME", "WALK_FORWARD", "OUT_OF_SAMPLE", "REGRESSION"):
        raw = f"test-only {kind} validation report".encode()
        report_hash = hashlib.sha256(raw).hexdigest()
        reports[report_hash] = raw
        validations.append(
            OfflineValidationEvidence(
                kind=kind,
                passed=True,
                tested_artifact_hash=artifact.artifact_hash,
                dataset_hash=artifact.dataset_hash,
                report_hash=report_hash,
                completed_at=now - timedelta(days=2),
            )
        )
    promotion = PromotionEvidence(
        artifact_id="baseline-v1",
        artifact_hash=artifact.artifact_hash,
        proposed_by="researcher",
        validations=tuple(validations),
        approval=IndependentApproval(
            reviewer_id="independent-reviewer",
            approved=True,
            reviewed_artifact_hash=artifact.artifact_hash,
            reviewed_validation_hashes=tuple(reports),
            reviewed_at=now - timedelta(days=1),
        ),
    )
    return QualifiedLinearModel(artifact=artifact, promotion=promotion), reports


def test_registry_requires_hash_verified_manual_independent_promotion_and_withdrawal(
    store: ResearchStore,
):
    registry = ModelRegistry(store)
    model, reports = model_fixture()
    with pytest.raises(StoreError, match="report bytes"):
        registry.register(model, {})
    record = registry.register(model, reports)
    assert registry.register(model, reports) == record
    with pytest.raises(StoreError):
        registry.load_active(record, model.artifact.artifact_hash, utc_now())
    with pytest.raises(StoreError, match="manual"):
        registry.promote(record, reviewer_id="independent-reviewer", manual=False)
    with pytest.raises(StoreError, match="operator"):
        registry.promote(record, reviewer_id="researcher", manual=True)
    before = utc_now() - timedelta(seconds=1)
    registry.promote(record, reviewer_id="independent-reviewer", manual=True)
    assert registry.load_active(record, model.artifact.artifact_hash, utc_now()) == model
    prediction_cutoff = utc_now()
    with pytest.raises(StoreError):
        registry.load_active(record, model.artifact.artifact_hash, before)
    with pytest.raises(StoreError):
        registry.load_active(record, "b" * 64, utc_now())
    registry.withdraw(
        record, reviewer_id="independent-reviewer", reason="regime validation expired"
    )
    with pytest.raises(StoreError):
        registry.load_active(record, model.artifact.artifact_hash, utc_now())
    with pytest.raises(StoreError):
        registry.load_active(record, model.artifact.artifact_hash, prediction_cutoff)
    with pytest.raises(DatabaseError, match="immutable"):
        with store.engine.begin() as connection:
            connection.execute(model_registry.update().values(payload={"replacement": True}))


def completed(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    run_once(store, build_runtime("demo"), worker_id="service-fixture", mode="demo")
    return job["id"]


def test_web_alert_is_atomic_with_packet_idempotent_and_workspace_scoped(store: ResearchStore):
    job_id = completed(store)
    outbox = AlertOutbox(store.for_workspace("private"))
    assert len(outbox.list_web()) == 1
    assert outbox.list_web()[0]["payload"]["informational_only"]
    with ThreadPoolExecutor(max_workers=4) as executor:
        identities = list(
            executor.map(
                lambda _: outbox.enqueue(job_id, "RESEARCH_COMPLETED", event_key="decision"),
                range(4),
            )
        )
    assert len(set(identities)) == 1
    assert len(outbox.list_web()) == 1
    assert AlertOutbox(store.for_workspace("other")).list_web() == []
    with pytest.raises(StoreError):
        AlertOutbox(store.for_workspace("other")).enqueue(
            job_id, "SIGNAL_EXPIRED", event_key="expiry"
        )


def test_external_alerts_remain_blocked_without_idempotent_provider(store: ResearchStore):
    job_id = completed(store)
    outbox = AlertOutbox(store)
    alert = outbox.enqueue(job_id, "RESEARCH_COMPLETED", event_key="email", channel="email")
    assert outbox.deliver_one()
    with store.engine.connect() as connection:
        assert (
            connection.scalar(select(alert_outbox.c.state).where(alert_outbox.c.id == alert))
            == "BLOCKED_CONFIGURATION"
        )


def test_alert_parallel_delivery_claims_once(store: ResearchStore):
    job_id = completed(store)
    outbox = AlertOutbox(store)
    outbox.enqueue(job_id, "RESEARCH_COMPLETED", event_key="email", channel="email")
    calls = []

    class TestProvider:
        supports_idempotency = True

        def deliver(self, message, *, idempotency_key, timeout_seconds):
            calls.append(idempotency_key)
            assert timeout_seconds == 20

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: outbox.deliver_one({"email": TestProvider()}), range(4)))
    assert len(calls) == 1


def test_replay_uses_original_timestamp_and_never_modifies_packet(store: ResearchStore):
    job_id = completed(store)
    original = store.get_job(job_id)["packet"]
    replay = replay_decision(
        store.for_workspace("private"), job_id, money_version="test-upgrade", git_sha="abcdef0"
    )
    assert replay["decision_timestamp"] == original["issued_at"]
    assert replay["changed"] is False
    assert replay["scope"] == "DETERMINISTIC_GATES_ONLY"
    assert store.get_job(job_id)["packet"] == original
    with pytest.raises(StoreError):
        replay_decision(
            store.for_workspace("other"), job_id, money_version="test", git_sha="unknown"
        )
    with pytest.raises(DatabaseError, match="immutable"):
        with store.engine.begin() as connection:
            connection.execute(replay_runs.delete())


def test_partial_first_pass_recovery_reuses_snapshot_and_only_invokes_missing_firms(
    store: ResearchStore,
):
    runtime = build_runtime("demo")
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("interrupted")
    owned = store.for_claim(claim)
    instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
    owned.save_artifact(job["id"], "eligibility", instrument)
    owned.update_stage(job["id"], "SNAPSHOT_BUILD")
    snapshot = runtime.snapshot_builder(instrument)
    owned.save_snapshot(job["id"], snapshot)
    owned.update_stage(job["id"], "FIRST_PASS_RESEARCH")
    report = runtime.firms[0].research(ResearchMandate(), snapshot)
    owned.save_report(job["id"], report.firm, report)
    owned.retry_job(job["id"], "PROVIDER_TIMEOUT")
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job["id"])
            .values(available_at=utc_now() - timedelta(seconds=1))
        )
    calls = []

    class CountFirm:
        def __init__(self, firm):
            self.firm = firm.firm
            self.delegate = firm

        def research(self, mandate, snapshot):
            calls.append(self.firm)
            return self.delegate.research(mandate, snapshot)

    resumed = replace(runtime, firms=tuple(CountFirm(firm) for firm in runtime.firms))
    run_once(store, resumed, worker_id="recovery", mode="demo")
    result = store.get_job(job["id"])
    assert result["status"] == "COMPLETE", result
    assert set(calls) == {"tradingagents", "ai_hedge_fund", "qlib"} - {report.firm}
    assert result["packet"]["snapshot_hash"] == snapshot.hash
    assert store.get_reports(job["id"])[report.firm] == report.model_dump(mode="json")
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(db.snapshots)) == 1


def test_concurrent_token_admission_never_overspends(store: ResearchStore):
    job = store.create_job("DEMO.L", ResearchMandate())
    manager = TokenBudgetManager(store, BudgetLimits(per_job=30, per_stage=30, per_agent=30))

    def reserve(index):
        try:
            manager.reserve(
                reservation_id=f"request-{index}",
                job_id=job["id"],
                stage="first_pass",
                agent="firm",
                provider="fixture",
                model="fixture",
                maximum_tokens=10,
                prompt_version="test-v1",
            )
            return True
        except ValueError as error:
            assert str(error) == "TOKEN_BUDGET_EXCEEDED"
            return False

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert sum(executor.map(reserve, range(8))) == 3


def test_outcomes_persist_idempotently_and_never_claim_trade_or_calibration(store: ResearchStore):
    job_id = completed(store)
    with pytest.raises(StoreError, match="published"):
        evaluate_signal_outcome(
            store,
            job_id,
            (),
            utc_now(),
            adjustment_basis="UNADJUSTED_NO_ACTIONS",
            dataset_version="test",
        )
    issued = utc_now() - timedelta(days=2)
    signal = ResearchSignal(
        research_id=job_id,
        ticker="DEMO.L",
        state="WATCH",
        issued_at=issued,
        valid_until=issued + timedelta(days=5),
        entry_low="1",
        entry_high="2",
        quote_currency="GBP",
        invalidation_conditions=("Material adverse filing",),
        event_invalidators=("New dilution",),
        potential_targets=("3",),
        assumed_capital_gbp="200",
        illustrative_allocation_gbp="100",
        modelled_downside_gbp="10",
        horizon_days=5,
    )
    # Test-only published fixture; no demo production path emits a signal.
    with store.engine.begin() as connection:
        connection.execute(
            db.signals.insert().values(
                job_id=job_id,
                payload=signal.model_dump(mode="json"),
                valid_until=signal.valid_until,
                created_at=issued,
            )
        )
    bar = OutcomeBar(
        evidence_id="future-research-price",
        evidence_hash="a" * 64,
        start_at=issued,
        end_at=issued + timedelta(days=1),
        available_at=issued + timedelta(days=1),
        open_gbp="1",
        low_gbp="1",
        high_gbp="2",
        close_gbp="2",
    )
    as_of = issued + timedelta(days=2)
    first = evaluate_signal_outcome(
        store,
        job_id,
        (bar,),
        as_of,
        adjustment_basis="UNADJUSTED_NO_ACTIONS",
        dataset_version="test",
    )
    second = evaluate_signal_outcome(
        store,
        job_id,
        (bar,),
        as_of,
        adjustment_basis="UNADJUSTED_NO_ACTIONS",
        dataset_version="test",
    )
    assert first == second
    assert first["basis"] == "PUBLISHED_RESEARCH_REFERENCE"
    assert first["calibration_qualified"] is False
    assert first["assumed_capital_gbp"] == "200"
    assert first["illustrative_allocation_gbp"] == "100"
    hypothetical = {item["calendar_days"]: item for item in first["hypothetical_gbp_outcomes"]}
    assert hypothetical[1]["basis"] == "RESEARCH_REFERENCE_PRICE_CHANGE_EXCLUDING_COSTS"
    assert Decimal(hypothetical[1]["price_change_gbp"]) == (
        Decimal("100") * Decimal(first["horizons"][0]["return_fraction"])
    )
    assert hypothetical[20]["price_change_gbp"] is None
    assert hypothetical[30]["price_change_gbp"] is None
    assert len(store.list_outcomes()) == 1
    with pytest.raises(StoreError):
        evaluate_signal_outcome(
            store.for_workspace("other"),
            job_id,
            (),
            as_of,
            adjustment_basis="UNADJUSTED_NO_ACTIONS",
            dataset_version="test",
        )
    with pytest.raises(StoreError, match="Adjusted"):
        evaluate_signal_outcome(
            store, job_id, (), as_of, adjustment_basis="SPLIT_ADJUSTED", dataset_version="test"
        )
    original = store.get_job(job_id)["packet"]
    proof = ResearchSnapshot.model_validate(store.get_evidence(job_id)["snapshot"]).evidence[0]
    store.for_workspace("private").invalidate_signal(job_id, reason="EVENT", evidence=proof)
    store.for_workspace("private").invalidate_signal(job_id, reason="EVENT", evidence=proof)
    assert store.get_job(job_id)["final_state"] == "EXPIRED"
    assert store.list_jobs()[0]["final_state"] == "EXPIRED"
    assert store.list_signals() == []
    assert store.list_signals(expired=True)[0]["final_state"] == "EXPIRED"
    assert store.get_job(job_id)["packet"] == original
    with store.engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(db.audit_events)
                .where(db.audit_events.c.event == "SIGNAL_INVALIDATED")
            )
            == 1
        )
    with pytest.raises(StoreError):
        store.for_workspace("other").invalidate_signal(job_id, reason="EVENT", evidence=proof)


def test_circuit_late_success_cannot_close_a_newer_open_circuit(store: ResearchStore):
    circuit = ProviderCircuit(store, threshold=1, cooldown_seconds=60)
    entered, release = threading.Event(), threading.Event()

    def delayed_success():
        entered.set()
        assert release.wait(5)
        return "earlier-request"

    def fail():
        raise ProviderFailure("PROVIDER_UNAVAILABLE", retryable=True)

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(circuit.call, "fixture-provider", delayed_success)
        assert entered.wait(5)
        try:
            with pytest.raises(ProviderFailure):
                circuit.call("fixture-provider", fail)
        finally:
            release.set()
        assert pending.result() == "earlier-request"
    with pytest.raises(ProviderFailure, match="CIRCUIT_OPEN"):
        circuit.call("fixture-provider", lambda: "must-not-run")


def test_circuit_allows_only_one_half_open_probe(store: ResearchStore):
    circuit = ProviderCircuit(store, threshold=1, cooldown_seconds=60)

    def fail():
        raise ProviderFailure("PROVIDER_UNAVAILABLE", retryable=True)

    with pytest.raises(ProviderFailure):
        circuit.call("probe-provider", fail)
    with store.engine.begin() as connection:
        connection.execute(
            provider_state.update()
            .where(provider_state.c.id == "probe-provider")
            .values(open_until=utc_now() - timedelta(seconds=1))
        )
    entered, release = threading.Event(), threading.Event()

    def probe():
        entered.set()
        assert release.wait(5)
        return True

    with ThreadPoolExecutor(max_workers=1) as executor:
        pending = executor.submit(circuit.call, "probe-provider", probe)
        assert entered.wait(5)
        try:
            with pytest.raises(ProviderFailure, match="CIRCUIT_OPEN"):
                circuit.call("probe-provider", lambda: "must-not-run")
        finally:
            release.set()
        assert pending.result() is True
    assert circuit.call("probe-provider", lambda: "recovered") == "recovered"
