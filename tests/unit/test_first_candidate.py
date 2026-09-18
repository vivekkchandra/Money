"""Synthetic temporary evidence only; preparation never qualifies production."""

import json
from datetime import timedelta

import pytest
from test_bulk_universe import NOW, Broker, instrument
from test_universe_providers import Fetcher

from money.data.security import ProviderFailure, SafeFetcher
from money.qualification import first_candidate, universe
from money.qualification.core import QualificationContext, json_bytes
from money.qualification.universe_progress import CURSOR
from money.qualification.universe_providers import BulkProviderEnricher


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "utc_now", lambda: NOW)
    monkeypatch.setattr(first_candidate, "utc_now", lambda: NOW)
    return QualificationContext(
        tmp_path / "bundle",
        tmp_path,
        {
            "TRADING212_API_KEY": "synthetic-first-account-key",
            "TRADING212_API_SECRET": "synthetic-first-account-secret",
            "EODHD_API_KEY": "synthetic-first-provider-key",
            "COMPANIES_HOUSE_API_KEY": "synthetic-first-company-key",
        },
        NOW,
    )


def prepare(ctx, *, instruments=None, fetcher=None):
    source = (
        instruments
        if instruments is not None
        else [instrument(ticker="FIXl_EQ", shortName="FIX", name="Fixture PLC")]
    )
    transport = fetcher or Fetcher()
    worker = BulkProviderEnricher(
        ctx,
        fetcher=transport,
        sleep=lambda _: None,
        clock=lambda: ctx.now,
    )
    master = universe.finalize_universe(ctx, broker=Broker(source), enricher=worker)
    ctx.write_json(
        first_candidate.SELECTION,
        {
            "trading212_id": source[0]["ticker"],
            "rationale": "Synthetic identifier completeness; not expected investment return.",
        },
    )
    return master, transport


def files(ctx):
    return {
        str(path.relative_to(ctx.root)): path.read_bytes()
        for path in ctx.root.rglob("*")
        if path.is_file()
    }


def deny_network(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline preparation attempted network I/O")

    monkeypatch.setattr(SafeFetcher, "get", forbidden)
    monkeypatch.setattr(SafeFetcher, "json", forbidden)


def test_offline_preparation_verifies_exact_bytes_without_mutating_reviews_or_universe(
    ctx, monkeypatch
):
    master, _ = prepare(ctx)
    ctx.write_json(CURSOR, {"resume_after": "UNRELATED", "note": "Synthetic cursor"})
    before = files(ctx)
    ctx.environ = {}  # Genuine saved bytes may be inspected without copying credentials.
    deny_network(monkeypatch)

    result = first_candidate.prepare_first_candidate(ctx)

    assert result["status"] == "PREPARATION_ONLY"
    assert result["trading212_id"] == "FIXl_EQ"
    assert result["isin"] == "GB00BH4HKS39"
    assert result["provider_symbol"] == "FIX.LSE"
    assert result["quote_currency"] == "GBX"
    assert result["membership"]["observed_at"] == master["observed_at"]
    assert result["provider_retry"] == {"status": "NOT_REQUESTED", "network_requests": 0}
    assert not result["eligibility_granted"]
    assert not result["production_qualified"]
    assert not result["reviews_modified"]
    assert not result["master_universe_modified"]
    assert result["first_pass"] == "NOT_RUN_PREREQUISITES_REQUIRED"
    assert result["lean"] == "NOT_RUN_FIRST_PASS_LOCK_REQUIRED"
    assert not result["available_evidence"]["rights_approved_by_preparation"]
    after = files(ctx)
    assert after.keys() == before.keys() | {
        first_candidate.OUTPUT,
        "outputs/first-candidate-lean-readiness.json",
    }
    readiness = result["lean_preparation"]
    assert readiness["status"] == "NOT_EXECUTED"
    assert readiness["lean_mandatory"]
    assert readiness["input_contract_valid"] is False
    assert readiness["input_errors"]
    assert not readiness["historical_publication_verified"]
    assert "FIRST_PASS_LOCKED" in readiness["execution_prerequisite"]
    assert all(after[path] == raw for path, raw in before.items())
    assert not (ctx.root / "manifest.json").exists()
    assert not (ctx.root / "outputs/snapshot.json").exists()


@pytest.mark.parametrize("target", ["broker", "provider_report", "provider_dataset", "search"])
def test_corrupt_saved_evidence_never_produces_preparation(ctx, monkeypatch, target):
    master, _ = prepare(ctx)
    row = master["stocks"][0]
    if target == "broker":
        descriptor = ctx.read_json(master["provenance"]["response_artifacts"]["instruments"][1])
        path = descriptor["chunks"][0][1]
    elif target == "provider_report":
        path = row["provider_reports"]["eodhd"]["report_ref"]["path"]
    elif target == "provider_dataset":
        item = row["provider_reports"]["eodhd"]["report"]["datasets"][0]
        path = "artifacts/" + item["artifact_path"]
    else:
        path = next(
            ref["path"]
            for ref in row["provider_evidence"]
            if (ctx.read_json(ref["path"]) or {})
            .get("request", {})
            .get("path", "")
            .startswith("/api/search/")
        )
    ctx.write_bytes(path, b"tampered synthetic evidence bytes")
    deny_network(monkeypatch)
    with pytest.raises((ValueError, ProviderFailure)):
        first_candidate.prepare_first_candidate(ctx)
    assert not (ctx.root / first_candidate.OUTPUT).exists()


@pytest.mark.parametrize("case", ["duplicate", "gbp", "etf", "absent"])
def test_only_unambiguous_raw_gbx_stock_can_be_prepared(ctx, monkeypatch, case):
    source = [instrument(ticker="FIXl_EQ", shortName="FIX", name="Fixture PLC")]
    if case == "duplicate":
        source.append(dict(source[0], ticker="OTHERl_EQ"))
    elif case == "gbp":
        source[0]["currencyCode"] = "GBP"
    elif case == "etf":
        source[0]["type"] = "ETF"
    prepare(ctx, instruments=source)
    if case == "absent":
        selection = ctx.read_json(first_candidate.SELECTION)
        selection["trading212_id"] = "ABSENTl_EQ"
        ctx.write_json(first_candidate.SELECTION, selection)
    deny_network(monkeypatch)
    with pytest.raises(ValueError, match="UNAMBIGUOUS_GBX_STOCK_REQUIRED"):
        first_candidate.prepare_first_candidate(ctx)
    assert not (ctx.root / first_candidate.OUTPUT).exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("isin", "AU000000CLA6"),
        ("quote_currency", "GBP"),
        ("name", "Different PLC"),
        ("instrument_type", "ETF"),
    ],
)
def test_derived_identity_cannot_reuse_an_unmodified_raw_row_hash(ctx, monkeypatch, field, value):
    master, _ = prepare(ctx)
    original_hash = master["stocks"][0]["instrument_row_sha256"]
    master["stocks"][0][field] = value
    assert master["stocks"][0]["instrument_row_sha256"] == original_hash
    ctx.write_json(universe.MASTER, master)
    deny_network(monkeypatch)
    with pytest.raises(ValueError, match="SAVED_ROW_MISMATCH"):
        first_candidate.prepare_first_candidate(ctx)
    assert not (ctx.root / first_candidate.OUTPUT).exists()


def test_expired_live_membership_blocks_even_requested_provider_refresh(ctx, monkeypatch):
    prepare(ctx)
    ctx.now = NOW + timedelta(hours=24)
    deny_network(monkeypatch)
    with pytest.raises(ValueError, match="CURRENT_LIVE_MEMBERSHIP_REQUIRED"):
        first_candidate.prepare_first_candidate(ctx, refresh_providers=True)


@pytest.mark.parametrize(
    "missing", ["TRADING212_API_KEY", "TRADING212_API_SECRET", "EODHD_API_KEY"]
)
def test_refresh_requires_existing_environment_credentials_and_binding(ctx, monkeypatch, missing):
    prepare(ctx)
    ctx.environ = {name: value for name, value in ctx.environ.items() if name != missing}
    deny_network(monkeypatch)
    result = first_candidate.prepare_first_candidate(ctx, refresh_providers=True)
    expected = (
        "EODHD_CREDENTIAL_REQUIRED"
        if missing == "EODHD_API_KEY"
        else "CURRENT_TRADING212_CREDENTIAL_BINDING_REQUIRED"
    )
    assert result["provider_retry"]["status"] == expected
    assert result["provider_retry"]["network_requests"] == 0
    assert not result["eligibility_granted"]


def test_mismatched_account_binding_cannot_refresh_selected_candidate(ctx, monkeypatch):
    prepare(ctx)
    ctx.environ = {**ctx.environ, "TRADING212_API_KEY": "different-synthetic-account-key"}
    deny_network(monkeypatch)
    with pytest.raises(ValueError, match="CURRENT_LIVE_MEMBERSHIP_REQUIRED"):
        first_candidate.prepare_first_candidate(ctx, refresh_providers=True)


def test_bounded_refresh_invokes_existing_enricher_for_only_selected_stock(ctx, monkeypatch):
    class DeniedGeneral(Fetcher):
        def json(self, url, *, headers=None):
            if "/fundamentals/" in url:
                self.calls.append(("/api/fundamentals/FIX.LSE", {}))
                raise ProviderFailure("PROVIDER_ACCESS_DENIED", http_status=403)
            return super().json(url, headers=headers)

    transport = DeniedGeneral()
    prepare(ctx, fetcher=transport)
    transport.calls.clear()
    ctx.now += timedelta(minutes=6)  # Bounded negative-cache expiry, not evidence refresh.
    before = files(ctx)
    instances = []

    def factory(context, **kwargs):
        instances.append(dict(kwargs))
        kwargs.setdefault("clock", lambda: context.now)
        return BulkProviderEnricher(context, fetcher=transport, sleep=lambda _: None, **kwargs)

    monkeypatch.setattr(first_candidate, "BulkProviderEnricher", factory)
    result = first_candidate.prepare_first_candidate(ctx, refresh_providers=True, max_requests=1)
    assert len(instances) == 2
    assert instances[0]["offline"] is True
    assert instances[1]["max_requests"] == 1
    assert transport.calls == [("/api/fundamentals/FIX.LSE", {})]
    assert result["provider_retry"]["network_requests"] == 1
    assert result["provider_retry"]["status"] == "OBSERVATIONS_ONLY"
    assert "PROVIDER_ACCESS_DENIED" in result["provider_retry"]["reasons"]
    assert not result["eligibility_granted"]
    after = files(ctx)
    protected = {path for path in before if path.startswith(("inputs/", "reviews/"))}
    protected |= {universe.MASTER, "status.json", CURSOR} & before.keys()
    assert all(after[path] == before[path] for path in protected)
    assert all(ctx.environ["EODHD_API_KEY"].encode() not in raw for raw in after.values())


@pytest.mark.parametrize("budget", [0, 13, -1])
def test_invalid_request_budget_fails_before_any_work(ctx, budget):
    with pytest.raises(ValueError, match="BUDGET_INVALID"):
        first_candidate.prepare_first_candidate(ctx, refresh_providers=True, max_requests=budget)


def test_cli_holds_existing_qualification_lock_and_sanitizes_provider_failures(
    ctx, monkeypatch, capsys
):
    called = []
    monkeypatch.setattr(first_candidate, "QualificationContext", lambda *_args: ctx)

    def blocked(*args, **kwargs):
        with pytest.raises(ValueError, match="QUALIFICATION_ALREADY_RUNNING"), ctx.locked():
            pytest.fail("Second context acquired the same lock")
        called.append(True)
        raise ProviderFailure(ctx.environ["EODHD_API_KEY"])

    monkeypatch.setattr(first_candidate, "prepare_first_candidate", blocked)
    assert first_candidate.main([]) == 2
    assert called == [True]
    output = capsys.readouterr()
    assert "FIRST_CANDIDATE_BLOCKED" in output.out
    assert ctx.environ["EODHD_API_KEY"] not in output.out + output.err
    assert "Traceback" not in output.out + output.err


def test_synthetic_provider_report_bytes_are_unchanged_by_preparation(ctx):
    master, _ = prepare(ctx)
    ref = master["stocks"][0]["provider_reports"]["eodhd"]["report_ref"]
    before = ctx.read_bytes(ref["path"])
    result = first_candidate.prepare_first_candidate(ctx)
    assert ctx.read_bytes(ref["path"]) == before
    assert result["available_evidence"]["historical_publication_verified"] is False
    assert result["available_evidence"]["financial_documents_verified"] is False
    assert json.loads(before)["retrieved_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert json_bytes(result) == ctx.read_bytes(first_candidate.OUTPUT)


def test_disabled_qlib_mode_does_not_remove_mandatory_lean_prerequisites(ctx):
    prepare(ctx)
    ctx.write_json("outputs/research-mode.json", {"qlib_enabled": False})
    result = first_candidate.prepare_first_candidate(ctx)
    readiness = result["lean_preparation"]
    assert readiness["recorded_qlib_enabled"] is False
    assert readiness["lean_mandatory"]
    assert readiness["status"] == "NOT_EXECUTED"
    assert readiness["input_contract_valid"] is False
    assert readiness["required_independent_audits"] == [
        "historical_eligibility",
        "survivorship",
        "corporate_actions",
        "costs",
    ]
    assert not (ctx.root / "outputs/lean-result.json").exists()


def rewrite_report(ctx, master, change):
    row = master["stocks"][0]
    report = row["provider_reports"]["eodhd"]["report"]
    change(row, report)
    digest, path = ctx.artifact(report)
    row["provider_reports"]["eodhd"]["report_ref"] = {"sha256": digest, "path": path}
    ctx.write_json(universe.MASTER, master)


@pytest.mark.parametrize("field,value", [("exchange", "WRONG"), ("exchange_ticker", "WRONG")])
def test_linked_report_identity_must_agree_with_actual_cached_provider_code(
    ctx, monkeypatch, field, value
):
    master, _ = prepare(ctx)

    def change(row, report):
        row["identifiers"][field] = value
        report["samples"][0][field] = value

    rewrite_report(ctx, master, change)
    deny_network(monkeypatch)
    with pytest.raises(ValueError, match="PROVIDER_IDENTITY_MISMATCH"):
        first_candidate.prepare_first_candidate(ctx)
    assert not (ctx.root / first_candidate.OUTPUT).exists()


@pytest.mark.parametrize("missing", ["artifact_hash", "artifact_path", "both"])
def test_retrieved_dataset_without_linked_artifact_cannot_be_described_as_verified(
    ctx, monkeypatch, missing
):
    master, _ = prepare(ctx)

    def change(_row, report):
        item = report["datasets"][0]
        assert item["status"] == "RETRIEVED"
        for field in ("artifact_hash", "artifact_path"):
            if missing in {field, "both"}:
                item[field] = None

    rewrite_report(ctx, master, change)
    deny_network(monkeypatch)
    with pytest.raises(ValueError):
        first_candidate.prepare_first_candidate(ctx)
    assert not (ctx.root / first_candidate.OUTPUT).exists()


def expiring_provider_fixture(ctx, monkeypatch):
    prepare(ctx)
    refreshed_at = NOW + timedelta(hours=23)
    ctx.now = refreshed_at
    monkeypatch.setattr(universe, "utc_now", lambda: refreshed_at)
    master, _ = prepare(ctx)
    assert master["observed_at"] == refreshed_at.isoformat()
    assert master["stocks"][0]["identifiers"]["verified_at"] == NOW.isoformat().replace(
        "+00:00", "Z"
    )
    ctx.now = NOW + timedelta(hours=25)
    return master


def test_offline_expired_provider_evidence_remains_dated_not_current(ctx, monkeypatch):
    expiring_provider_fixture(ctx, monkeypatch)
    deny_network(monkeypatch)
    before = files(ctx)
    result = first_candidate.prepare_first_candidate(ctx)
    assert result["membership"]["current"]
    assert result["available_evidence"] is None
    assert result["verified_provider_identity"] is None
    assert result["verified_observation_evidence"]["current"] is False
    assert result["verified_observation_evidence"]["retrieved_at"] == NOW.isoformat()
    assert result["provider_retry"]["network_requests"] == 0
    assert all(files(ctx)[path] == raw for path, raw in before.items())


def test_refresh_can_replace_expired_provider_observations_only_with_new_bounded_bytes(
    ctx, monkeypatch
):
    expiring_provider_fixture(ctx, monkeypatch)
    transport = Fetcher()
    refresh_at = ctx.now
    before = files(ctx)

    def factory(context, **kwargs):
        kwargs.setdefault("clock", lambda: context.now)
        return BulkProviderEnricher(context, fetcher=transport, sleep=lambda _: None, **kwargs)

    monkeypatch.setattr(first_candidate, "BulkProviderEnricher", factory)
    result = first_candidate.prepare_first_candidate(ctx, refresh_providers=True, max_requests=12)
    assert 0 < result["provider_retry"]["network_requests"] <= 12
    assert len(transport.calls) == result["provider_retry"]["network_requests"]
    assert result["available_evidence"]["current"] is True
    assert result["available_evidence"]["retrieved_at"] == refresh_at.isoformat()
    assert result["provider_symbol"] == "FIX.LSE"
    assert not result["eligibility_granted"]
    assert not result["production_qualified"]
    assert all("order" not in path for path, _ in transport.calls)
    after = files(ctx)
    assert after[universe.MASTER] == before[universe.MASTER]
    assert all(
        after[path] == raw
        for path, raw in before.items()
        if path.startswith(("inputs/", "reviews/"))
    )


def test_failed_current_provider_retry_never_reuses_old_current_ready_evidence(ctx, monkeypatch):
    expiring_provider_fixture(ctx, monkeypatch)
    transport = Fetcher()
    transport.fail = ProviderFailure("PROVIDER_UNAVAILABLE", http_status=402)

    def factory(context, **kwargs):
        kwargs.setdefault("clock", lambda: context.now)
        return BulkProviderEnricher(context, fetcher=transport, sleep=lambda _: None, **kwargs)

    monkeypatch.setattr(first_candidate, "BulkProviderEnricher", factory)
    result = first_candidate.prepare_first_candidate(ctx, refresh_providers=True, max_requests=1)
    assert result["provider_retry"]["network_requests"] == 1
    assert result["provider_retry"]["reasons"] == ["PROVIDER_PAYMENT_REQUIRED"]
    assert result["available_evidence"] is None
    assert result["verified_provider_identity"] is None
    assert not result["eligibility_granted"]
