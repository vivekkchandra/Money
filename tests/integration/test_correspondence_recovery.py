"""Synthetic paid-call fixtures only: no native runtime or live qualification.

The existing dialect fixture repeats these cases on real PostgreSQL in CI. The
explicit live-tagged response is a schema fixture, never an external LLM result.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_budget_recovery import store as _dialect_store

from money.adapters.upstream import UPSTREAM_SHAS
from money.api.settings import Settings
from money.crews.cross_examination import (
    ChallengeInvocation,
    ChallengeResponse,
    ChallengeVerification,
)
from money.flows.research import build_runtime
from money.research.budgets import BudgetLimits
from money.research.correspondence import LiveCorrespondence
from money.research.live import InferenceSelection
from money.schemas.contracts import AuditFinding, ResearchMandate, Usage, content_hash, utc_now
from money.storage import LeaseLost
from money.storage import models as db
from money.storage.production_models import budget_reservations
from money.storage.store import StoreError

production_dialect_store = _dialect_store


@pytest.fixture
def correspondence_store(production_dialect_store):
    """Reuse the existing isolated SQLite/PostgreSQL migration/cleanup fixture."""
    return production_dialect_store


@pytest.fixture
def selection():
    return InferenceSelection(
        provider="synthetic-contract-fixture",
        model="synthetic-model",
        endpoint="https://fixture.invalid/v1",
        credential_environment_variable="UNUSED_TEST_KEY",
        maximum_prompt_bytes=1000,
        max_output_tokens=256,
    )


def prepare(store, barrier="ready"):
    runtime, mandate = build_runtime("demo"), ResearchMandate()
    job = store.create_job("DEMO.L", mandate)
    claim = store.claim_job("correspondence-fixture")
    owned = store.for_claim(claim)
    instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
    owned.save_artifact(job["id"], "eligibility", instrument)
    owned.update_stage(job["id"], "SNAPSHOT_BUILD")
    snapshot = runtime.snapshot_builder(instrument)
    owned.save_snapshot(job["id"], snapshot)
    owned.update_stage(job["id"], "FIRST_PASS_RESEARCH")
    reports = tuple(firm.research(mandate, snapshot) for firm in runtime.firms)
    if barrier != "prelock":
        for report in reports:
            owned.save_report(job["id"], report.firm, report)
        owned.lock_first_pass(job["id"])
        owned.update_stage(job["id"], "LEAN_VALIDATION")
        lean = runtime.validate(snapshot, reports)
        if barrier != "missing_lean":
            owned.save_artifact(job["id"], "lean", lean)
        owned.update_stage(job["id"], "CREWAI_AUDIT")
        audit = runtime.audit(snapshot, reports, lean)
        if barrier == "incomplete_audit":
            audit = audit.model_copy(update={"completed": False})
        owned.save_cio_artifacts(job["id"], audit, runtime.red_team(snapshot, reports))
        if barrier != "before_cross_stage":
            owned.update_stage(job["id"], "CROSS_EXAMINATION")
    return job["id"], owned, snapshot, reports


def service(owned, limits=None):
    # invoke does not need a promoted model; the real examine path does. No native
    # capability or fake production manifest is constructed by this test helper.
    return LiveCorrespondence(owned, SimpleNamespace(budgets=limits or BudgetLimits()), None)


def fixture_response(snapshot, reports, selection):
    report = next(item for item in reports if item.firm == "ai_hedge_fund")
    return ChallengeResponse(
        challenge_id="synthetic-test-challenge",
        respondent="ai_hedge_fund",
        snapshot_hash=snapshot.hash,
        original_report_hash=content_hash(report),
        position="INSUFFICIENT_EVIDENCE",
        explanation="Synthetic contract response, not live research.",
        evidence_ids=(),
        invocation=ChallengeInvocation(
            component="ai_hedge_fund",
            provider=selection.provider,
            model=selection.model,
            prompt_version="money-native-correspondence-v1",
            upstream_sha=UPSTREAM_SHAS["ai_hedge_fund"],
            calls=1,
            usage=Usage(input_tokens=5, output_tokens=2),
            runtime="live",
        ),
    )


def invoke(engine, job_id, selection, snapshot, response, operation=None):
    return engine.invoke(
        job_id,
        "ai_hedge_fund",
        selection,
        1,
        (snapshot, response.challenge_id, 1),
        ChallengeResponse,
        operation or (lambda: response),
    )


def charges(store):
    with store.engine.connect() as connection:
        return list(connection.execute(select(budget_reservations)).mappings())


def sealed_calls(store, job_id):
    with store.engine.connect() as connection:
        return list(
            connection.scalars(
                select(db.artifacts.c.payload).where(
                    db.artifacts.c.job_id == job_id, db.artifacts.c.kind.startswith("cross_call_")
                )
            )
        )


def resume(store, owned, job_id):
    assert owned.retry_job(job_id, "PROVIDER_TIMEOUT")
    with store.engine.begin() as connection:
        connection.execute(
            db.jobs.update()
            .where(db.jobs.c.id == job_id)
            .values(available_at=utc_now() - timedelta(seconds=1))
        )
    claim = store.claim_job("correspondence-recovered")
    return store.for_claim(claim)


@pytest.mark.parametrize(
    "barrier", ("prelock", "before_cross_stage", "missing_lean", "incomplete_audit")
)
def test_correspondence_requires_all_durable_barriers_before_paid_call(
    correspondence_store, selection, barrier
):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store, barrier)
    response = fixture_response(snapshot, reports, selection)

    def forbidden():
        raise AssertionError("paid call ran before the durable barrier")

    with pytest.raises(ValueError, match="BARRIER_INCOMPLETE"):
        invoke(service(owned), job_id, selection, snapshot, response, forbidden)
    assert charges(store) == [] and sealed_calls(store, job_id) == []


def test_sealed_call_survives_restart_without_second_paid_invocation(
    correspondence_store, selection
):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store)
    response = fixture_response(snapshot, reports, selection)
    calls = []

    def paid_fixture():
        calls.append("called")
        assert charges(store)[0]["actual_tokens"] is None  # reserve precedes paid work
        return response

    assert invoke(service(owned), job_id, selection, snapshot, response, paid_fixture) == response
    recovered = resume(store, owned, job_id)
    assert (
        invoke(service(recovered), job_id, selection, snapshot, response, paid_fixture) == response
    )
    assert calls == ["called"]
    assert len(charges(store)) == 1 and charges(store)[0]["actual_tokens"] == 7
    assert len(sealed_calls(store, job_id)) == 1


def test_crash_after_seal_reconciles_exact_correspondence_usage(
    correspondence_store, selection, monkeypatch
):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store)
    response = fixture_response(snapshot, reports, selection)
    engine = service(owned)

    def interrupted(*args):
        raise TimeoutError("synthetic crash after immutable seal")

    monkeypatch.setattr(engine.budget, "settle", interrupted)
    with pytest.raises(TimeoutError):
        invoke(engine, job_id, selection, snapshot, response)
    assert len(sealed_calls(store, job_id)) == 1
    assert charges(store)[0]["actual_tokens"] is None
    recovered = resume(store, owned, job_id)
    restored = service(recovered)
    restored.budget.reconcile_job(job_id)
    assert charges(store)[0]["actual_tokens"] == 7

    def forbidden():
        raise AssertionError("sealed response must not be paid for twice")

    assert invoke(restored, job_id, selection, snapshot, response, forbidden) == response


def test_budget_rejection_happens_before_paid_call(correspondence_store, selection):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store)
    response = fixture_response(snapshot, reports, selection)

    def forbidden():
        raise AssertionError("paid call ran without a reservation")

    with pytest.raises(ValueError, match="TOKEN_BUDGET_EXCEEDED"):
        invoke(
            service(owned, BudgetLimits(per_job=10)),
            job_id,
            selection,
            snapshot,
            response,
            forbidden,
        )
    assert charges(store) == [] and sealed_calls(store, job_id) == []


def test_unsealed_failure_remains_charged_and_same_attempt_cannot_call_twice(
    correspondence_store, selection
):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store)
    response = fixture_response(snapshot, reports, selection)
    calls = []

    def interrupted():
        calls.append("called")
        raise TimeoutError("synthetic ambiguous paid failure")

    with pytest.raises(TimeoutError):
        invoke(service(owned), job_id, selection, snapshot, response, interrupted)
    with pytest.raises(ValueError, match="INVOCATION_ALREADY_RESERVED"):
        invoke(service(owned), job_id, selection, snapshot, response, interrupted)
    assert calls == ["called"]
    assert sealed_calls(store, job_id) == []
    recovered = resume(store, owned, job_id)
    assert invoke(service(recovered), job_id, selection, snapshot, response) == response
    reservations = charges(store)
    assert len(reservations) == 2
    assert sorted(row["actual_tokens"] or -1 for row in reservations) == [-1, 7]
    assert (
        sum(
            row["actual_tokens"] if row["actual_tokens"] is not None else row["reserved_tokens"]
            for row in reservations
        )
        == 2280 + 7
    )


@pytest.mark.parametrize(
    "change",
    (
        {"provider": "wrong"},
        {"model": "wrong"},
        {"runtime": "demo"},
        {"runtime": "deterministic"},
        {"component": "tradingagents"},
        {"calls": 2},
    ),
)
def test_wrong_invocation_metadata_cannot_seal_and_remains_pessimistically_charged(
    correspondence_store, selection, change
):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store)
    response = fixture_response(snapshot, reports, selection)
    response = response.model_copy(
        update={"invocation": response.invocation.model_copy(update=change)}
    )
    with pytest.raises(ValueError, match="INVOCATION_MISMATCH"):
        invoke(service(owned), job_id, selection, snapshot, response)
    assert sealed_calls(store, job_id) == []
    reservation = charges(store)[0]
    assert reservation["actual_tokens"] is None
    assert reservation["reserved_tokens"] == 1000 + 256 + 1024
    assert reservation["payload"]["cost_status"] == "unknown"


def test_stale_worker_cannot_call_or_settle_sealed_correspondence(correspondence_store, selection):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store)
    response = fixture_response(snapshot, reports, selection)
    invoke(service(owned), job_id, selection, snapshot, response)
    resume(store, owned, job_id)
    with pytest.raises(LeaseLost):
        invoke(service(owned), job_id, selection, snapshot, response)
    with pytest.raises(LeaseLost):
        service(owned).budget.reconcile_job(job_id)
    assert len(charges(store)) == 1


def test_foreign_workspace_cannot_access_correspondence_even_with_same_claim(
    correspondence_store, selection
):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store)
    foreign = store.for_workspace("foreign").for_claim(owned.claim)
    response = fixture_response(snapshot, reports, selection)
    with pytest.raises(StoreError):
        invoke(service(foreign), job_id, selection, snapshot, response)
    assert charges(store) == []


def test_worker_losing_lease_during_paid_call_cannot_seal_result(correspondence_store, selection):
    store = correspondence_store
    job_id, owned, snapshot, reports = prepare(store)
    response = fixture_response(snapshot, reports, selection)

    def expired_fixture():
        with store.engine.begin() as connection:
            connection.execute(
                db.jobs.update()
                .where(db.jobs.c.id == job_id)
                .values(lease_until=utc_now() - timedelta(seconds=1))
            )
        return response

    with pytest.raises(LeaseLost):
        invoke(service(owned), job_id, selection, snapshot, response, expired_fixture)
    assert sealed_calls(store, job_id) == []
    assert charges(store)[0]["actual_tokens"] is None


@pytest.mark.parametrize(
    "calls,usage,accepted",
    (
        (0, Usage(input_tokens=0, output_tokens=0), True),
        (1, Usage(input_tokens=0, output_tokens=0), False),
        (0, Usage(input_tokens=1, output_tokens=0), False),
        (0, Usage(input_tokens=0, output_tokens=1), False),
        (0, Usage(), False),
    ),
)
def test_crewai_deterministic_verification_requires_zero_calls_and_measured_zero_usage(
    correspondence_store, selection, calls, usage, accepted
):
    store = correspondence_store
    job_id, owned, snapshot, _ = prepare(store)
    verification = ChallengeVerification(
        finding=AuditFinding(
            auditor="CIO Contradiction Analyst",
            state="UNSUPPORTED",
            explanation="Synthetic deterministic adverse finding; no inference called.",
        ),
        invocation=ChallengeInvocation(
            component="crewai",
            provider=selection.provider,
            model=selection.model,
            prompt_version="money-deterministic-challenge-verification-v1",
            upstream_sha="synthetic-test-only",
            calls=calls,
            usage=usage,
            runtime="deterministic",
        ),
    )

    def request():
        return service(owned).invoke(
            job_id,
            "crewai",
            selection,
            1,
            (snapshot, "synthetic-verification"),
            ChallengeVerification,
            lambda: verification,
        )

    if accepted:
        assert request() == verification
        assert len(sealed_calls(store, job_id)) == 1
        assert charges(store)[0]["actual_tokens"] == 0
        # No recorded monetary cost is still unknown, never silently zero.
        assert charges(store)[0]["payload"]["cost_status"] == "unknown"
    else:
        with pytest.raises(ValueError, match="INVOCATION_MISMATCH"):
            request()
        assert sealed_calls(store, job_id) == []
        assert charges(store)[0]["actual_tokens"] is None


def test_compute_process_binds_runtime_services_to_the_active_claim(
    correspondence_store, monkeypatch
):
    from money import worker

    store = correspondence_store
    job = store.create_job("DEMO.L", ResearchMandate())
    claim = store.claim_job("assembly-fixture")
    assert claim is not None
    settings = Settings.model_validate(
        {
            "money_env": "test",
            "money_research_mode": "demo",
            "database_url": store.database_url,
            "research_api_token": "synthetic-assembly-token-not-for-deployment",
        }
    )
    factory_claims, checkpoints, executions = [], [], []

    def factory(mode, *, store, **kwargs):
        factory_claims.append(store.claim)
        # LiveCorrespondence captures this store; it must have actual owned access,
        # not merely receive a claim later through a different run_once store.
        checkpoints.append(store.get_checkpoint(job["id"]))
        return object()

    def no_compute(store, runtime, **kwargs):
        executions.append(kwargs["claimed"])
        return True

    monkeypatch.setattr(worker.os, "setsid", lambda: None)
    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    monkeypatch.setattr("money.flows.research.build_runtime", factory)
    monkeypatch.setattr(worker, "run_once", no_compute)
    worker._compute_process(settings, claim)
    assert factory_claims == [claim]
    assert len(checkpoints) == 1 and checkpoints[0]["stage"] == "ELIGIBILITY_CHECK"
    assert executions == [claim]
