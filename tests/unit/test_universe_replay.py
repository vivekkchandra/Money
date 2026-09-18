"""Regression fixtures are synthetic temporary files, never qualification evidence."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from money.qualification import universe
from money.qualification.core import QualificationContext
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QualificationContext:
    monkeypatch.setattr(universe, "utc_now", lambda: NOW)
    return QualificationContext(tmp_path / "bundle", tmp_path, {}, NOW)


def stock(**changes: Any) -> dict[str, Any]:
    return {
        "ticker": "TESTl_EQ",
        "shortName": "TEST",
        "name": "Synthetic Example PLC",
        "isin": "GB00BH4HKS39",
        "currencyCode": "GBX",
        "type": "STOCK",
        **changes,
    }


def seed_old(
    ctx: QualificationContext, instruments: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    raw = json.dumps(
        instruments
        or [
            stock(),
            stock(ticker="FOREIGNl_EQ", shortName="FOREIGN", isin="US0378331005"),
            stock(ticker="GBP_EQ", currencyCode="GBP"),
        ]
    ).encode()
    references = {
        "instruments": universe._store_response(ctx, raw),
        "exchanges": universe._store_response(ctx, b"[]"),
    }
    binding = hashlib.sha256(b"synthetic historical credential binding").hexdigest()
    source = {
        "retrieval_environment": "live",
        "retrieved_at": NOW.isoformat(),
        "response_artifacts": references,
        "credential_binding_sha256": binding,
        "instrument_response_hash": hashlib.sha256(raw).hexdigest(),
        "exchange_response_hash": hashlib.sha256(b"[]").hexdigest(),
    }
    ctx.write_json(
        "state/bulk-broker-metadata.json",
        {
            "status": "CURRENT",
            "observed_at": NOW.isoformat(),
            "attempted_at": NOW.isoformat(),
            "responses": references,
            "credential_binding_sha256": binding,
        },
    )
    ctx.write_json(
        universe.MASTER,
        {
            "version": "money-uk-isa-universe-v1",
            "provenance": source,
            "stocks": [{"reasons": ["VENUE_COUNTRY_NOT_VERIFIED"]}],
            "summary": {"stocks_gbp_gbx": 3, "uk_venue_stocks": 0},
        },
    )
    ctx.write_json("outputs/universe-provenance.json", source)
    for path in (
        "outputs/universe-review-queue.json",
        "state/provider-stage.json",
        "outputs/providers-result.json",
    ):
        ctx.write_json(
            path,
            {
                "complete": True,
                "reasons": ["VENUE_COUNTRY_NOT_VERIFIED"],
                "candidate_counts": {"GBP": 1, "GBX": 2},
            },
        )
    ctx.write_json(universe.MODE, {"version": "money-bulk-universe-v1"})
    return source


def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    from money.data.security import SafeFetcher

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Offline replay attempted external I/O")

    monkeypatch.setattr(universe.Trading212MetadataProvider, "metadata_response", forbidden)
    monkeypatch.setattr(SafeFetcher, "json", forbidden)


def seed_recorded_live(ctx: QualificationContext) -> dict[str, Any]:
    source = seed_old(ctx)
    source.update(
        universe_policy_version="money-t212-gbx-stock-universe-v2",
        scope="LIVE_RETRIEVAL",
        credential_binding_verified_this_run=True,
        raw_instruments=3,
        gbx_stocks=2,
        account_context="UNVERIFIED",
    )
    ctx.write_json("outputs/universe-provenance.json", source)
    ctx.write_json("inputs/universe/account-scope.json", {"review": {"status": "UNRESOLVED"}})
    return source


def test_saved_live_policy_reclassification_preserves_evidence_without_new_authentication(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from money.qualification.universe_status import live_metadata_state

    source = seed_recorded_live(ctx)
    review = ctx.read_bytes("inputs/universe/account-scope.json")
    raw_cache = ctx.read_bytes("state/bulk-broker-metadata.json")
    raw_evidence = {path: path.read_bytes() for path in (ctx.root / "artifacts").iterdir()}
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, reclassify_saved=True)
    assert result["status"] == "RECLASSIFIED"
    assert result["scope"] == "SAVED_LIVE_DERIVED_RECLASSIFICATION"
    assert result["provenance"]["credential_binding_verified_this_run"] is False
    assert result["observed_at"] == source["retrieved_at"]
    assert result["summary"]["gbx_stocks"] == 2
    assert result["summary"]["identity_valid"] == 2
    assert result["eligibility_reviews"] == []
    assert result["summary"]["qualified"] == 0
    assert "UNRESOLVED_ISA_SCOPE" not in json.dumps(result)
    assert "ACCOUNT_ISA_SCOPE_REVIEW_REQUIRED" not in json.dumps(result)
    assert "ACCOUNT_AND_CURRENT_BUY_PROVENANCE_REQUIRED" not in json.dumps(result)
    metadata = live_metadata_state(ctx, result)
    assert metadata["current"] is False
    assert metadata["source_current"] is True
    assert metadata["current_process_binding_verified"] is False
    assert ctx.read_bytes("inputs/universe/account-scope.json") == review
    assert ctx.read_bytes("state/bulk-broker-metadata.json") == raw_cache
    assert all(path.read_bytes() == raw for path, raw in raw_evidence.items())
    again = universe.finalize_universe(ctx, reclassify_saved=True)
    assert again["status"] == "RECLASSIFIED"
    assert live_metadata_state(ctx, again)["source_current"] is True


def test_saved_policy_migration_replaces_retired_status_actions_without_approvals(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_recorded_live(ctx)
    ctx.write_json(
        "status.json",
        {
            "status": "QUALIFICATION BLOCKED",
            "blockers": [
                {"code": "BULK_UNIVERSE_NO_QUALIFIED_MEMBERS", "action": "Resolve the bulk account, provider and ethical evidence queue"},
                {"code": "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED", "action": "Old account-scope action"},
                {"code": "ACCOUNT_ISA_SCOPE_REVIEW_REQUIRED", "action": "Old review requirement"},
                {"code": "ACCOUNT_AND_CURRENT_BUY_PROVENANCE_REQUIRED", "action": "Old account requirement"},
            ],
        },
    )
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, reclassify_saved=True)
    state = ctx.read_json("status.json")
    blockers = {item["code"]: item["action"] for item in state["blockers"]}
    assert "ACCOUNT_ISA_SCOPE_REVIEW_REQUIRED" not in blockers
    assert "ACCOUNT_AND_CURRENT_BUY_PROVENANCE_REQUIRED" not in blockers
    assert "account" not in blockers["BULK_UNIVERSE_NO_QUALIFIED_MEMBERS"]
    assert "rights" in blockers["BULK_UNIVERSE_NO_QUALIFIED_MEMBERS"]
    assert "ethical" in blockers["BULK_UNIVERSE_NO_QUALIFIED_MEMBERS"]
    assert "rebuilt offline" in blockers["SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED"]
    assert "not authenticated by a new live refresh" in blockers["SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED"]
    assert state["production_ready"] is False
    assert result["eligibility_reviews"] == []


@pytest.mark.parametrize("problem", ["binding", "raw", "replayed-source", "unauthenticated-source"])
def test_saved_reclassification_cannot_bypass_retrieval_integrity(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, problem: str
) -> None:
    source = seed_recorded_live(ctx)
    if problem == "binding":
        ctx.environ = {"TRADING212_API_KEY": "synthetic-different-key", "TRADING212_API_SECRET": "synthetic-secret"}
    elif problem == "raw":
        descriptor = ctx.read_json(source["response_artifacts"]["instruments"][1])
        ctx.write_bytes(descriptor["chunks"][0][1], b"tampered synthetic fixture")
    elif problem == "replayed-source":
        source["scope"] = "SAVED_RESPONSE_REPLAY_ONLY"
    else:
        source["credential_binding_verified_this_run"] = False
    ctx.write_json("outputs/universe-provenance.json", source)
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, reclassify_saved=True)
    assert result["status"] == "REFRESH_FAILED"
    assert result["stocks"] == []
    assert result["production_qualified"] is False


def test_stale_recorded_live_source_reclassifies_only_as_expired(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from money.qualification.universe_status import live_metadata_state

    seed_recorded_live(ctx)
    ctx.now = NOW + timedelta(hours=25)
    monkeypatch.setattr(universe, "utc_now", lambda: ctx.now)
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, reclassify_saved=True)
    assert result["summary"]["states"] == {"EXPIRED": 2}
    assert result["eligibility_reviews"] == []
    metadata = live_metadata_state(ctx, result)
    assert metadata["current"] is False
    assert metadata["source_current"] is False


def test_saved_v1_state_rebuilds_all_active_projections_without_network(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = seed_old(ctx)
    evidence_before = {str(path): path.read_bytes() for path in (ctx.root / "artifacts").iterdir()}
    raw_cache = ctx.read_bytes("state/bulk-broker-metadata.json")
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, replay_saved=True)
    assert result["status"] == "REPLAYED"
    assert result["scope"] == "SAVED_RESPONSE_REPLAY_ONLY"
    assert result["summary"]["gbx_stocks"] == 2
    assert result["summary"]["identity_valid"] == 2
    assert result["summary"]["identity_unresolved"] == 0
    assert result["summary"]["provider_stage_input_count"] == 2
    assert result["summary"]["qualified"] == 0
    assert result["eligibility_reviews"] == []
    assert result["production_qualified"] is False
    assert result["observed_at"] == source["retrieved_at"]
    assert result["provenance"]["credential_binding_sha256"] == source["credential_binding_sha256"]
    assert result["provenance"]["credential_binding_verified_this_run"] is False
    assert "account_type_returned_by_api" not in result["provenance"]
    for path in (
        universe.MASTER,
        "outputs/universe-provenance.json",
        "outputs/universe-review-queue.json",
        "outputs/providers-result.json",
        "state/provider-stage.json",
        universe.MODE,
    ):
        value = ctx.read_json(path)
        assert value["universe_policy_version"] == UNIVERSE_POLICY_VERSION
        assert "VENUE_COUNTRY_NOT_VERIFIED" not in json.dumps(value)
        assert "uk_venue_stocks" not in value.get("summary", {})
    projection = ctx.read_json("outputs/providers-result.json")
    assert projection["candidate_counts"] == {"GBX": 2}
    assert projection["provider_stage_input_count"] == len(projection["provider_stage_inputs"]) == 2
    assert projection["instruments"] == []  # Research instruments still need every gate.
    assert not projection["complete"]
    queue = ctx.read_json("outputs/universe-review-queue.json")
    assert "account_scope" not in queue
    assert queue["venue_review_required"] is False
    assert all(
        "ACCOUNT_AND_CURRENT_BUY_PROVENANCE_REQUIRED" not in row["reasons"]
        for row in queue["instruments"]
    )
    assert not list((ctx.root / "inputs/instruments").glob("*.json"))
    assert ctx.read_bytes("state/bulk-broker-metadata.json") == raw_cache
    assert all(Path(path).read_bytes() == raw for path, raw in evidence_before.items())


def test_replay_preserves_expiry_and_never_authenticates_retrieval(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_old(ctx)
    later = NOW + timedelta(hours=25)
    ctx.now = later
    monkeypatch.setattr(universe, "utc_now", lambda: later)
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, replay_saved=True)
    assert result["valid_until"] == (NOW + timedelta(hours=24)).isoformat()
    assert result["summary"]["states"]["EXPIRED"] == 2
    assert result["summary"]["qualified"] == 0
    assert result["eligibility_reviews"] == []
    assert result["observed_at"] == NOW.isoformat()


def test_conflicting_members_never_enter_provider_enrichment_but_valid_peer_does(
    ctx: QualificationContext,
) -> None:
    instruments = [
        stock(),
        stock(ticker="DUP_EQ"),
        stock(ticker="OTHER_EQ", shortName="OTHER", isin="US0378331005"),
    ]
    calls = []

    class Broker:
        def metadata_response(self, kind: str) -> tuple[bytes, tuple[Any, ...]]:
            value = instruments if kind == "instruments" else []
            return json.dumps(value).encode(), tuple(value)

    class Enricher:
        def enrich(self, row: dict[str, Any]) -> dict[str, Any]:
            calls.append(row["trading212_id"])
            assert row["qualification_state"] == "UNRESOLVED_PROVIDER_MAPPING"
            assert row["identity_valid"] is True
            return row

    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    assert calls == ["OTHER_EQ"]
    assert result["summary"]["gbx_stocks"] == 3
    assert result["summary"]["identity_unresolved"] == 2
    assert result["summary"]["provider_stage_input_count"] == 1
    assert result["summary"]["qualified"] == 0


def test_corrupt_raw_response_cannot_be_replayed_as_evidence(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = seed_old(ctx)
    descriptor = ctx.read_json(source["response_artifacts"]["instruments"][1])
    ctx.write_bytes(descriptor["chunks"][0][1], b"Corrupted synthetic raw response")
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, replay_saved=True)
    assert result["status"] == "REFRESH_FAILED"
    assert result["stocks"] == []
    assert ctx.read_json("outputs/providers-result.json")["complete"] is False
    assert ctx.read_json("outputs/universe-review-queue.json")["instruments"] == []


def test_live_failure_clears_previous_provider_ready_display(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_old(ctx)
    universe.finalize_universe(ctx, replay_saved=True)
    ready = ctx.read_json("outputs/providers-result.json")
    ready["complete"] = True
    ctx.write_json("outputs/providers-result.json", ready)
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx)
    assert result["status"] == "REFRESH_FAILED"
    assert ctx.read_json("outputs/providers-result.json")["complete"] is False
    assert ctx.read_json("state/provider-stage.json")["provider_stage_inputs"] == []


def test_replay_falls_back_to_archived_source_metadata_without_copying_classifications(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = seed_old(ctx)
    ctx.write_json("state/bulk-broker-metadata.json", {"status": "ATTEMPTED"})
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, replay_saved=True)
    assert result["status"] == "REPLAYED"
    assert result["observed_at"] == source["retrieved_at"]
    assert result["summary"]["identity_valid"] == 2
    assert all("VENUE_COUNTRY_NOT_VERIFIED" not in json.dumps(row) for row in result["stocks"])


def test_replay_prefers_last_successful_provenance_over_older_migration_source(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_old(ctx)
    universe.finalize_universe(ctx, replay_saved=True)
    current = ctx.read_json("outputs/universe-provenance.json")
    current["retrieved_at"] = (NOW - timedelta(minutes=1)).isoformat()
    ctx.write_json("outputs/universe-provenance.json", current)
    old = ctx.read_json("state/universe-rebuild-source.json")
    old["source"]["retrieved_at"] = (NOW - timedelta(hours=3)).isoformat()
    ctx.write_json("state/universe-rebuild-source.json", old)
    ctx.write_json("state/bulk-broker-metadata.json", {"status": "ATTEMPTED"})
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, replay_saved=True)
    assert result["observed_at"] == current["retrieved_at"]
    assert result["provenance"]["instrument_response_hash"] == current["instrument_response_hash"]
    assert result["eligibility_reviews"] == []


def test_credential_free_run_preserves_genuine_broker_cache_but_invalidates_membership(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_old(ctx)
    before = ctx.read_bytes("state/bulk-broker-metadata.json")
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx)
    assert result["status"] == "REFRESH_FAILED"
    assert result["stocks"] == []
    assert ctx.read_bytes("state/bulk-broker-metadata.json") == before
    assert ctx.read_json("outputs/universe-review-work.json")["status"] == "REFRESH_FAILED"


@pytest.mark.parametrize("marker", [None, b"invalid stale marker"])
def test_provider_entrypoint_never_routes_existing_bulk_universe_to_legacy_gbp_policy(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch, marker: bytes | None
) -> None:
    from money.qualification.providers import run_provider_stages

    ctx.write_json(universe.MASTER, {"version": "money-uk-isa-universe-v1"})
    if marker is not None:
        ctx.write_bytes(universe.MODE, marker)
    expected = {"complete": False, "bulk": True}
    monkeypatch.setattr(universe, "run_bulk_provider_stages", lambda _: expected)
    assert run_provider_stages(ctx) == expected


def test_full_provider_stage_keeps_enrichment_inputs_distinct_from_approved_instruments(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from money.qualification import providers

    seed_old(ctx)
    master = universe.finalize_universe(ctx, replay_saved=True)
    monkeypatch.setattr(universe, "finalize_universe", lambda _: master)
    for name in ("_additional_sources", "_financial_documents", "_filter_source_coverage"):
        monkeypatch.setattr(providers, name, lambda *_: None)
    result = universe.run_bulk_provider_stages(ctx)
    assert result["universe_policy_version"] == UNIVERSE_POLICY_VERSION
    assert result["provider_stage_input_count"] == 2
    assert result["candidate_counts"] == {"GBX": 2}
    assert result["eligible_counts"] == {"GBX": 0}
    assert result["instruments"] == result["qualified_universe"] == []
    assert result["complete"] is False
    assert ctx.read_json("state/provider-stage.json") == json.loads(json.dumps(result))
    assert ctx.read_json("outputs/providers-result.json") == json.loads(json.dumps(result))


@pytest.mark.parametrize("problem", ["declared-too-large", "too-many-chunks", "underdeclared-size"])
def test_saved_response_rejects_unbounded_or_inconsistent_chunk_descriptors(
    ctx: QualificationContext, problem: str
) -> None:
    chunk = ctx.artifact(b"[]")
    descriptor = {
        "kind": "exact-response-chunks-v1",
        "bytes": 2,
        "sha256": hashlib.sha256(b"[]").hexdigest(),
        "chunks": [chunk],
    }
    if problem == "declared-too-large":
        descriptor["bytes"] = universe.MAX_BROKER_RESPONSE_BYTES + 1
    elif problem == "too-many-chunks":
        descriptor["chunks"] = [chunk] * 15
    else:
        descriptor["bytes"] = 1
    with pytest.raises(ValueError, match="BROKER_RESPONSE_CACHE_INVALID"):
        universe._restore_response(ctx, ctx.artifact(descriptor))


def test_provenance_exchange_hash_mismatch_discards_only_optional_enrichment(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = seed_old(ctx)
    source["exchange_response_hash"] = hashlib.sha256(
        b"different synthetic source bytes"
    ).hexdigest()
    ctx.write_json("outputs/universe-provenance.json", source)
    ctx.write_json("state/bulk-broker-metadata.json", {"status": "ATTEMPTED"})
    forbid_network(monkeypatch)
    result = universe.finalize_universe(ctx, replay_saved=True)
    assert result["status"] == "REPLAYED"
    assert result["provenance"]["exchange_response_hash"] is None
    assert result["summary"]["identity_valid"] == 2
    assert result["summary"]["provider_stage_input_count"] == 2
