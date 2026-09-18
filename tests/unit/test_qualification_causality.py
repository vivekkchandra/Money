"""Temporary synthetic state tests; none of these bytes qualify production."""

from datetime import timedelta

import pytest
from test_bulk_universe import NOW, Broker, Enricher

from money.qualification import runner, universe
from money.qualification.core import QualificationContext
from money.qualification.diagnostics import write_qualification_diagnostics
from money.qualification.snapshot import run_snapshot_stage
from money.qualification.universe_status import live_metadata_state, reconcile_universe_status


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(universe, "utc_now", lambda: NOW)
    return QualificationContext(
        tmp_path / "bundle",
        tmp_path,
        {
            "TRADING212_API_KEY": "synthetic-account-key",
            "TRADING212_API_SECRET": "synthetic-account-secret",
        },
        NOW,
    )


def test_successful_live_refresh_reconciles_only_proved_metadata_failure(ctx):
    ctx.write_json(
        "status.json",
        {
            "status": "QUALIFICATION BLOCKED",
            "blockers": [
                {"code": "TRADING212_LIVE_METADATA_REQUIRED", "action": "old failure"},
                {"code": "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED", "action": "old refresh action"},
                {"code": "CHROMADB_SECURITY_ADVISORIES", "action": "must remain"},
            ],
        },
    )
    result = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    state = ctx.read_json("status.json")
    codes = {item["code"] for item in state["blockers"]}
    assert "TRADING212_LIVE_METADATA_REQUIRED" not in codes
    assert "CHROMADB_SECURITY_ADVISORIES" in codes
    assert "QUALIFICATION_STAGE_RESULTS_STALE" in codes
    assert (
        state["universe_metadata"]["source_provenance_sha256"] == result["universe_provenance"][0]
    )
    assert state["production_ready"] is False
    assert state["downstream_stage_results_current"] is False
    assert state["updated_at"] == NOW.isoformat()
    assert "metadata is current" in next(
        item["action"]
        for item in state["blockers"]
        if item["code"] == "SNAPSHOT_CURRENT_INSTRUMENT_REQUIRED"
    )


@pytest.mark.parametrize("change", ["expired", "binding", "provenance", "replay", "raw_bytes"])
def test_invalid_or_replayed_retrieval_never_clears_metadata_blocker(ctx, change):
    master = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    ctx.write_json(
        "status.json",
        {
            "blockers": [
                {"code": "TRADING212_LIVE_METADATA_REQUIRED", "action": "current failure"},
            ]
        },
    )
    if change == "expired":
        ctx.now = NOW + timedelta(hours=24)
    elif change == "binding":
        ctx.environ = {**ctx.environ, "TRADING212_API_KEY": "different-synthetic-key"}
    elif change == "provenance":
        master["provenance"]["raw_instruments"] = 99
    elif change == "replay":
        master["scope"] = "SAVED_RESPONSE_REPLAY_ONLY"
    elif change == "raw_bytes":
        ref = master["provenance"]["response_artifacts"]["instruments"]
        descriptor = ctx.read_json(ref[1])
        ctx.write_bytes(descriptor["chunks"][0][1], b"tampered synthetic fixture")
    reconcile_universe_status(ctx, master, live_refresh=True)
    assert not live_metadata_state(ctx, master)["current"]
    assert "TRADING212_LIVE_METADATA_REQUIRED" in {
        item["code"] for item in ctx.read_json("status.json")["blockers"]
    }


def test_fresh_zero_eligibility_is_not_reported_as_failed_metadata_refresh(ctx):
    master = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    result = run_snapshot_stage(ctx, universe._provider_projection(master))
    assert result["complete"] is False
    assert result["blocked_by"] == "ELIGIBILITY_QUALIFICATION"
    assert result["universe_metadata"]["current"] is True
    assert "refresh alone cannot" in ctx.blockers[-1]["action"]
    assert not (ctx.root / "outputs/snapshot.json").exists()


def test_empty_qualified_universe_is_not_a_bundle_serializer_error(ctx):
    assert runner.assemble_manifest(ctx, [{"qualified_universe": [], "instruments": []}]) is None
    assert {item["code"] for item in ctx.blockers} == {"RELEASE_APPROVAL_REQUIRED"}
    assert not (ctx.root / "manifest.json").exists()
    review = ctx.read_json("reviews/release.json")
    assert review["reviewed_by"] is None
    assert review["reviewed_at"] is None


def test_dag_disabled_qlib_is_absent_and_lean_is_mandatory(ctx):
    ctx.environ = {**ctx.environ, "MONEY_QLIB_ENABLED": "false"}
    master = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    source = ctx.read_bytes(universe.MASTER, universe.MAX_MASTER_BYTES)
    review = ctx.read_bytes("inputs/universe/account-scope.json")
    diagnostic = write_qualification_diagnostics(ctx, master=master)
    by_id = {item["id"]: item for item in diagnostic["nodes"]}
    assert "qlib" not in by_id
    assert "account_scope" not in by_id
    assert "identity" in by_id["eligibility"]["depends_on"]
    assert "lean" in by_id["cio_red_team"]["depends_on"]
    assert "first_pass" in by_id["lean"]["depends_on"]
    assert diagnostic["lean_mandatory"] is True
    assert not diagnostic["approval_granted"]
    assert ctx.read_bytes(universe.MASTER, universe.MAX_MASTER_BYTES) == source
    assert ctx.read_bytes("inputs/universe/account-scope.json") == review

    # A dependency walk must terminate: this is a DAG, not a display-only cycle.
    def walk(name, ancestors):
        assert name not in ancestors
        for parent in by_id[name]["depends_on"]:
            walk(parent, ancestors | {name})

    walk("hosted_acceptance", set())


def test_offline_diagnosis_cannot_claim_current_credential_or_clear_failed_refresh(ctx):
    master = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    ctx.environ = {}
    assert live_metadata_state(ctx, master)["current"] is True
    assert live_metadata_state(ctx, master)["current_process_binding_verified"] is False
    ctx.write_json(
        "status.json",
        {
            "blockers": [
                {"code": "TRADING212_LIVE_METADATA_REQUIRED", "action": "failed new refresh"},
            ]
        },
    )
    reconcile_universe_status(ctx, master, live_refresh=False)
    assert "TRADING212_LIVE_METADATA_REQUIRED" in {
        item["code"] for item in ctx.read_json("status.json")["blockers"]
    }


def test_budget_deferred_ohlcv_is_missing_not_expired_and_never_qualifies(ctx):
    class MissingBars(Enricher):
        def enrich(self, source):
            row = super().enrich(source)
            row["provider_datasets"]["eodhd:ohlcv"].update(
                status="FAILED",
                latest_observation=None,
                record_count=0,
            )
            row["provider_reasons"] = ["PROVIDER_REQUEST_BUDGET_EXHAUSTED"]
            return row

    master = universe.finalize_universe(ctx, broker=Broker(), enricher=MissingBars())
    row = master["stocks"][0]
    assert row["qualification_state"] != "UNRESOLVED_ISA_SCOPE"
    assert row["evidence_freshness"] == "PROVIDER_OBSERVATIONS_MISSING"
    assert "CURRENT_TIMESTAMPED_PROVIDER_OBSERVATIONS_REQUIRED" in row["reasons"]
    assert master["eligibility_reviews"] == []
    assert master["summary"]["qualified"] == 0


def test_explicit_qlib_mode_wins_over_stale_diagnostic_receipt(ctx):
    ctx.write_json("outputs/research-mode.json", {"qlib_enabled": False})
    ctx.environ = {**ctx.environ, "MONEY_QLIB_ENABLED": "true"}
    result = write_qualification_diagnostics(ctx)
    assert result["qlib_enabled"] is True
    assert result["requested_mode_matches_recorded"] is False
    assert "qlib" in {node["id"] for node in result["nodes"]}
    instructions = ctx.read_bytes("outputs/NEXT_ACTIONS.md").decode()
    assert "MONEY_QLIB_ENABLED=true MONEY_INFERENCE_CONFIG=" in instructions
    assert "MONEY_QLIB_ENABLED=false MONEY_INFERENCE_CONFIG=" not in instructions


def test_runner_status_links_to_exact_current_universe_not_tuple_serialization(ctx):
    master = universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    report = {"updated_at": NOW.isoformat(), "universe_provenance": master["universe_provenance"]}
    result = write_qualification_diagnostics(ctx, report=report)
    assert result["status_source_link_missing_or_changed"] is False


def test_successful_refresh_clears_same_context_failure_without_status_file(ctx):
    ctx.block("TRADING212_LIVE_METADATA_REQUIRED", "previous failed attempt")
    ctx.block("CHROMADB_SECURITY_ADVISORIES", "independent security failure")
    universe.finalize_universe(ctx, broker=Broker(), enricher=Enricher())
    assert {item["code"] for item in ctx.blockers} == {"CHROMADB_SECURITY_ADVISORIES"}


def test_current_dag_does_not_restore_retired_account_requirements_from_old_status(ctx):
    retired = ["ACCOUNT_ISA_SCOPE_REVIEW_REQUIRED", "ACCOUNT_AND_CURRENT_BUY_PROVENANCE_REQUIRED"]
    ctx.write_json("status.json", {"blockers": [
        *[{"code": code, "action": "Old policy only"} for code in retired],
        {"code": "CHROMADB_SECURITY_ADVISORIES", "action": "Must remain"},
    ]})
    before = ctx.read_bytes("status.json")
    result = write_qualification_diagnostics(ctx)
    nodes = {item["id"]: item for item in result["nodes"]}
    assert "account_scope" not in nodes
    codes = {code for item in result["nodes"] for code in item["reported_blockers"]}
    assert not set(retired) & codes
    assert "CHROMADB_SECURITY_ADVISORIES" in codes
    assert not result["approval_granted"]
    assert ctx.read_bytes("status.json") == before
    assert ctx.read_json("inputs/universe/account-scope.json") is None
