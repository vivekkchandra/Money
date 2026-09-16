"""Actual gateway attempts, native receipts and commercial ownership accounting."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError

from money.adapters.native_process import BoundedNativeRunner, NativeProcessPolicy
from money.data.resilience import ProviderCircuit
from money.data.security import ProviderFailure
from money.product.metering import CallMeter, admin_call_summary, call_metering
from money.product.models import usage_records
from money.research.budgets import BudgetLimits, TokenBudgetManager
from money.research.call_telemetry import InferenceReceipt, capture_calls, emit_calls
from money.research.inference import HTTPInference, InferenceConfiguration
from money.schemas.contracts import Contract, ResearchMandate, Usage, utc_now
from money.storage import LeaseLost, ResearchStore
from money.storage.models import jobs


@pytest.fixture(params=("sqlite", "postgres"))
def store(store, request):
    if request.param == "sqlite":
        yield store
        return
    raw = os.environ.get("TEST_DATABASE_URL")
    if not raw:
        pytest.skip("SKIPPED_EXTERNAL_INFRA: TEST_DATABASE_URL not configured")
    raw = raw.replace("postgresql://", "postgresql+psycopg://", 1)
    schema = f"money_calls_{uuid4().hex}"
    admin = create_engine(raw)
    with admin.begin() as connection:
        connection.execute(text(f"CREATE SCHEMA {schema}"))
    scoped = (
        make_url(raw)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    repository = ResearchStore(scoped)
    try:
        configuration = Config("alembic.ini")
        configuration.attributes["database_url"] = scoped
        command.upgrade(configuration, "head")
        yield repository
    finally:
        repository.engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
        admin.dispose()


def prepare(store, *, customer=False):
    job = store.for_workspace("tenant-a").create_job("DEMO.L", ResearchMandate())
    if customer:
        now = utc_now()
        with store.transaction() as connection:
            connection.execute(
                usage_records.insert().values(
                    job_id=job["id"],
                    workspace_id="tenant-a",
                    user_id="actual-customer",
                    period_start=now - timedelta(days=1),
                    period_end=now + timedelta(days=29),
                    plan="PRO",
                    max_tokens=10000,
                    max_seconds=100,
                    data_version="test",
                    created_at=now,
                )
            )
    owned = store.for_claim(store.claim_job("metering-worker"))
    manager = TokenBudgetManager(owned, BudgetLimits())
    reservation = f"{job['id']}:1:tradingagents"
    manager.reserve(
        reservation_id=reservation,
        job_id=job["id"],
        stage="FIRST_PASS_RESEARCH",
        agent="tradingagents",
        provider="fixture",
        model="test-model",
        maximum_tokens=1000,
        prompt_version="test-v1",
    )
    return job["id"], owned, manager, reservation


def gateway(monkeypatch, *, usage=None, mismatch=False):
    inference = HTTPInference(
        InferenceConfiguration(
            provider="fixture",
            model="test-model",
            endpoint="https://example.com/inference",
            api_key="test-only",
            input_gbp_per_million=Decimal("2"),
            output_gbp_per_million=Decimal("4"),
        )
    )
    monkeypatch.setattr(
        inference,
        "_post",
        lambda body, headers: {
            "model": "different" if mismatch else "test-model",
            "usage": usage if usage is not None else {"prompt_tokens": 20, "completion_tokens": 5},
            "choices": [{"finish_reason": "stop", "message": {"content": "fixture reply"}}],
        },
    )
    return inference


def rows(store):
    with store.engine.connect() as connection:
        return list(
            connection.execute(select(call_metering).order_by(call_metering.c.sequence)).mappings()
        )


def test_actual_gateway_hook_records_safe_tenant_attribution_without_double_count(
    store, monkeypatch
):
    job_id, owned, manager, reservation = prepare(store, customer=True)
    inference = gateway(monkeypatch)
    result = CallMeter(owned).invoke_reserved(
        reservation, lambda: inference.complete("private prompt", "secret research")
    )
    assert result == "fixture reply"
    manager.settle(reservation, inference.usage())
    records = rows(store)
    assert len(records) == 3
    receipt = next(row for row in records if row["kind"] == "INFERENCE_CALL")
    assert (receipt["job_id"], receipt["workspace_id"], receipt["user_id"]) == (
        job_id,
        "tenant-a",
        "actual-customer",
    )
    assert receipt["period_start"] is not None
    assert receipt["provider_calls"] == 1
    assert receipt["input_tokens"] == 20
    assert receipt["output_tokens"] == 5
    assert receipt["estimated_cost"] == Decimal("0.00006")
    assert receipt["actual_cost"] is None
    assert receipt["currency"] == "GBP"
    assert "private prompt" not in repr(records) and "secret research" not in repr(records)
    summary = admin_call_summary(store.for_workspace("tenant-a"))
    measured = next(group for group in summary["groups"] if group["kind"] == "INFERENCE_CALL")
    assert measured["input_tokens"] == 20 and measured["actual_cost_subtotal"] is None
    assert measured["unknown_actual_cost_records"] == 1
    assert summary["unfinished_invocations"] == 0
    assert admin_call_summary(store.for_workspace("tenant-b"))["groups"] == []


def test_unknown_usage_is_not_zero_and_failed_call_is_persisted(store, monkeypatch):
    _, owned, _, reservation = prepare(store)
    inference = gateway(monkeypatch, usage={}, mismatch=True)
    with pytest.raises(ValueError, match="MODEL_MISMATCH"):
        CallMeter(owned).invoke_reserved(reservation, lambda: inference.complete("system", "input"))
    receipt = next(row for row in rows(store) if row["kind"] == "INFERENCE_CALL")
    assert receipt["status"] == "FAILED"
    assert receipt["input_tokens"] is None and receipt["actual_cost"] is None
    assert receipt["error_code"] == "INFERENCE_FAILED"


def test_settlement_fallback_is_idempotent_and_explicitly_not_a_provider_call(store):
    _, _, manager, reservation = prepare(store)
    usage = Usage(input_tokens=10, output_tokens=3, cost_gbp=Decimal("0.01"))
    manager.settle(reservation, usage)
    manager.settle(reservation, usage)
    assert len(rows(store)) == 1
    measured = rows(store)[0]
    assert measured["kind"] == "REPORTED_USAGE" and measured["provider_calls"] is None
    assert measured["duration_ms"] is None and measured["actual_cost"] is None
    assert measured["estimated_cost"] == Decimal("0.01")
    with pytest.raises(ValueError, match="TOKEN_USAGE_CONFLICT"):
        manager.settle(reservation, Usage(input_tokens=99))


def test_duplicate_invocation_is_rejected_before_operation(store):
    _, owned, _, reservation = prepare(store)
    operations = []
    meter = CallMeter(owned)
    meter.invoke_reserved(reservation, lambda: operations.append(1))
    with pytest.raises(ValueError, match="ALREADY_STARTED"):
        meter.invoke_reserved(reservation, lambda: operations.append(2))
    assert operations == [1]


def test_concurrent_duplicate_invocation_only_starts_once(store):
    _, owned, _, reservation = prepare(store)
    operations = []

    def invoke(_):
        try:
            CallMeter(owned).invoke_reserved(reservation, lambda: operations.append(1))
            return "ran"
        except ValueError:
            return "duplicate"

    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(invoke, range(3)))
    assert sorted(outcomes) == ["duplicate", "duplicate", "ran"]
    assert operations == [1]


def test_stale_worker_cannot_append_completed_costs(store):
    job_id, owned, _, reservation = prepare(store)

    def lose_lease():
        with store.transaction() as connection:
            connection.execute(
                jobs.update().where(jobs.c.id == job_id).values(lease_token=str(uuid4()))
            )
        emit_calls(
            (
                InferenceReceipt(
                    provider="fixture",
                    model="test-model",
                    duration_seconds=1,
                    status="SUCCEEDED",
                    input_tokens=9,
                ),
            )
        )

    with pytest.raises(LeaseLost):
        CallMeter(owned).invoke_reserved(reservation, lose_lease)
    assert len(rows(store)) == 1
    assert rows(store)[0]["status"] == "STARTED"
    assert admin_call_summary(store)["unfinished_invocations"] == 1


def test_fence_and_budget_identity_checked_before_any_work(store):
    job_id, owned, _, reservation = prepare(store)
    operations = []
    with pytest.raises(ValueError, match="BUDGET_IDENTITY"):
        CallMeter(owned).invoke(
            job_id,
            component="tradingagents",
            provider="other",
            model="test-model",
            operation=lambda: operations.append(1),
            reservation_id=reservation,
        )
    with pytest.raises(Exception, match="RESOURCE_NOT_FOUND"):
        CallMeter(owned.for_workspace("tenant-b").for_claim(owned.claim)).invoke_reserved(
            reservation, lambda: operations.append(2)
        )
    assert operations == [] and rows(store) == []


def test_call_ledger_rejects_mutation_in_database(store):
    _, _, manager, reservation = prepare(store)
    manager.settle(reservation, Usage())
    for statement in (call_metering.update().values(provider="changed"), call_metering.delete()):
        with pytest.raises(DatabaseError, match="immutable"):
            with store.transaction() as connection:
                connection.execute(statement)


def test_admin_summary_is_bounded_and_non_content(store):
    _, _, manager, reservation = prepare(store)
    manager.settle(reservation, Usage())
    summary = admin_call_summary(store, days=1, limit=1)
    assert len(summary["groups"]) == 1
    assert not {"job_id", "user_id", "workspace_id", "prompt", "response"} & set(
        summary["groups"][0]
    )
    for kwargs in ({"days": 0}, {"days": 367}, {"limit": 201}, {"limit": 0}):
        with pytest.raises(ValueError):
            admin_call_summary(store, **kwargs)


def test_provider_operations_are_attributed_without_invented_http_calls(store):
    job_id, owned, _, _ = prepare(store, customer=True)
    circuit = ProviderCircuit(owned)
    assert circuit.call("fixture:filing", lambda: "safe fixture") == "safe fixture"

    def unavailable():
        raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True)

    with pytest.raises(ProviderFailure):
        circuit.call("fixture:filing", unavailable)
    measured = [row for row in rows(store) if row["status"] != "STARTED"]
    assert len(measured) == 2
    assert {row["status"] for row in measured} == {"SUCCEEDED", "FAILED"}
    assert all(row["kind"] == "PROVIDER_OPERATION" for row in measured)
    assert all(row["job_id"] == job_id and row["user_id"] == "actual-customer" for row in measured)
    assert all(row["provider_calls"] is None and row["actual_cost"] is None for row in measured)
    assert all(row["duration_ms"] >= 0 for row in measured)


def test_metering_and_offboarding_schema_remain_drift_free(store):
    configuration = Config("alembic.ini")
    configuration.attributes["database_url"] = store.database_url
    command.upgrade(configuration, "head")
    command.check(configuration)


def test_gateway_receipt_contract_rejects_hidden_payloads_and_invalid_costs():
    base = {"provider": "fixture", "model": "test", "duration_seconds": 1, "status": "SUCCEEDED"}
    for bad in (
        {"prompt": "secret"},
        {"actual_cost": "1"},
        {"duration_seconds": float("nan")},
        {"input_tokens": -1},
        {"provider": "a\nsecret"},
        {"error_code": "SECRET"},
    ):
        with pytest.raises(ValidationError):
            InferenceReceipt.model_validate(base | bad)


def test_receipt_capture_is_isolated_and_bounded():
    receipt = InferenceReceipt(
        provider="test", model="test", duration_seconds=0, status="SUCCEEDED"
    )
    with capture_calls() as outer:
        emit_calls((receipt,))
        with capture_calls() as inner:
            emit_calls((receipt, receipt))
        assert len(inner) == 2 and len(outer) == 1
        with pytest.raises(ValueError, match="LIMIT"):
            emit_calls([receipt] * 256)


class FixtureResult(Contract):
    value: str


def native_receipt_fixture():
    emit_calls(
        (
            InferenceReceipt(
                provider="fixture",
                model="test-model",
                duration_seconds=0.2,
                status="SUCCEEDED",
                input_tokens=17,
            ),
        )
    )
    return FixtureResult(value="safe fixture")


def test_actual_isolated_process_transports_only_bounded_receipts(store):
    _, owned, _, reservation = prepare(store)
    runner = BoundedNativeRunner(native_receipt_fixture, FixtureResult, NativeProcessPolicy())
    result = CallMeter(owned).invoke_reserved(reservation, runner)
    assert result.value == "safe fixture"
    assert next(row for row in rows(store) if row["kind"] == "INFERENCE_CALL")["input_tokens"] == 17
    with store.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(call_metering)) == 3
