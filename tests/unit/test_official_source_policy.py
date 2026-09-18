"""Synthetic policy integration regressions, confined to disposable test roots."""

from datetime import timedelta

import pytest
from test_filing_documents import NOW, prepare_snapshot_builder
from test_official_disclosure_runtime import official_manifest

from money.data.source_policy import IssuerSourcePolicy, issuer_source_policy
from money.qualification import providers, snapshot, universe
from money.qualification.core import QualificationContext
from money.qualification.universe_policy import (
    MODE,
    UNIVERSE_POLICY_VERSION,
    ensure_universe_policy,
)
from money.research import live
from money.schemas.contracts import ResearchSnapshot


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.setattr(live, "utc_now", lambda: NOW)
    return QualificationContext(
        tmp_path / "bundle", tmp_path,
        {"MONEY_ISSUER_SOURCE_POLICY": "official_disclosures", "MONEY_QLIB_ENABLED": "false"},
        NOW,
    )


@pytest.mark.parametrize("environ,expected", [
    ({}, IssuerSourcePolicy.COMPANIES_HOUSE),
    ({"MONEY_ISSUER_SOURCE_POLICY": "companies_house"}, IssuerSourcePolicy.COMPANIES_HOUSE),
    ({"MONEY_ISSUER_SOURCE_POLICY": "official_disclosures"}, IssuerSourcePolicy.OFFICIAL_DISCLOSURES),
])
def test_source_policy_is_explicit_with_compatible_default(environ, expected):
    assert issuer_source_policy(environ) == expected


@pytest.mark.parametrize("value", ["", "auto", "skip", "OFFICIAL_DISCLOSURES", " false "])
def test_invalid_source_policy_does_not_silently_disable_source_gates(value):
    with pytest.raises(ValueError, match="ISSUER_SOURCE_POLICY_INVALID"):
        issuer_source_policy({"MONEY_ISSUER_SOURCE_POLICY": value})


def test_source_policy_switch_rebuilds_only_derived_classification(ctx):
    legacy = QualificationContext(ctx.root, ctx.repo, {}, NOW)
    ensure_universe_policy(legacy)
    previous = {
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "issuer_source_policy": "companies_house",
        "instruments": [],
        "reasons": ["CURRENT_COMPANIES-HOUSE_FILING_OBSERVATION_REQUIRED"],
    }
    for name in ("outputs/trading212-gbx-stock-universe.json", "state/provider-stage.json"):
        legacy.write_json(name, previous)
    preserved = {
        "inputs/provider-rights/companies-house.json": b'{"review":{"status":"UNRESOLVED"}}',
        "inputs/supplemental-sources.json": b'{"review_files":[]}',
        "reviews/lean-inputs.json": b'{"review":"not signed"}',
        "state/bulk-provider-cache/issuer.json": b'{"observed_at":"original","raw":"fixture"}',
        "state/bulk-broker-metadata.json": b'{"observed_at":"original","hash":"unchanged"}',
    }
    for name, raw in preserved.items():
        legacy.write_bytes(name, raw)
    raw_evidence = legacy.artifact(b"synthetic original raw response bytes")
    old_derived = legacy.read_bytes("state/provider-stage.json")

    result = ensure_universe_policy(ctx)

    assert result["rebuilt"] and result["reason"] == "POLICY_MISMATCH"
    assert ctx.read_json(MODE)["issuer_source_policy"] == "official_disclosures"
    assert ctx.read_bytes("state/provider-stage.json") is None
    assert ctx.read_bytes(f"{result['archive_directory']}/state/provider-stage.json") == old_derived
    assert ctx.verify_artifact(*raw_evidence) == b"synthetic original raw response bytes"
    assert all(ctx.read_bytes(name) == raw for name, raw in preserved.items())
    assert not ensure_universe_policy(ctx)["rebuilt"]
    # Switching back must not retain admission decisions from the alternative.
    assert ensure_universe_policy(legacy)["rebuilt"]
    assert legacy.read_json(MODE)["issuer_source_policy"] == "companies_house"


def test_official_source_skips_ch_document_step_without_rewriting_legacy_review(ctx):
    previous = b'{"review":{"status":"UNRESOLVED"},"selections":[]}'
    ctx.write_bytes("inputs/financial-documents.json", previous)
    result = {"instruments": [], "provider_qualifications": []}
    providers._financial_documents(ctx, result, set())
    assert ctx.blockers == []
    assert ctx.read_bytes("inputs/financial-documents.json") == previous
    # Missing alternative evidence is still a real blocker, not an approval.
    providers._official_financial_documents(ctx, result)
    assert [item["code"] for item in ctx.blockers] == [
        "OFFICIAL_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED"
    ]
    assert providers._provider_stage_complete(ctx, result) is False
    instructions = ctx.read_json("inputs/official-disclosures.instructions.json")
    assert instructions["status"] == "UNRESOLVED"
    assert instructions["no_approval_granted"] is True


@pytest.mark.parametrize("relative", [
    "outputs/trading212-gbx-stock-universe.json",
    "outputs/universe-provenance.json",
    "outputs/universe-review-queue.json",
    "outputs/providers-result.json",
    "state/provider-stage.json",
])
@pytest.mark.parametrize("old_source_policy", [None, "companies_house"])
def test_missing_marker_does_not_hide_a_derived_source_policy_mismatch(
    ctx, relative, old_source_policy,
):
    prior = {
        "universe_policy_version": UNIVERSE_POLICY_VERSION,
        "complete": False,
        "instruments": [],
    }
    if old_source_policy is not None:
        prior["issuer_source_policy"] = old_source_policy
    ctx.write_json(relative, prior)
    evidence = ctx.artifact(b"synthetic source bytes survive partial marker loss")
    original = ctx.read_bytes(relative)
    result = ensure_universe_policy(ctx)
    assert result["rebuilt"]
    assert ctx.read_bytes(relative) is None
    assert ctx.read_bytes(f"{result['archive_directory']}/{relative}") == original
    assert ctx.verify_artifact(*evidence) == b"synthetic source bytes survive partial marker loss"


def test_official_source_qualification_can_complete_without_ch_but_with_all_evidence(ctx):
    manifest = official_manifest()
    result = {
        "instruments": [item.model_dump(mode="json") for item in manifest.reviewed_instruments],
        "provider_qualifications": [
            item.model_dump(mode="json") for item in manifest.provider_qualifications
        ],
    }
    assert not any(item["provider"] == "companies-house"
                   for item in result["provider_qualifications"])
    providers._official_financial_documents(ctx, result)
    assert len(result["instruments"]) == 1
    assert providers._provider_stage_complete(ctx, result)
    assert ctx.blockers == []


@pytest.mark.parametrize("missing", ["proof", "financial", "provider"])
def test_official_route_does_not_waive_financial_or_provider_qualification(ctx, missing):
    manifest = official_manifest()
    result = {
        "instruments": [item.model_dump(mode="json") for item in manifest.reviewed_instruments],
        "provider_qualifications": [
            item.model_dump(mode="json") for item in manifest.provider_qualifications
        ],
    }
    if missing == "proof":
        result["instruments"][0]["official_disclosures"] = []
    elif missing == "financial":
        result["instruments"][0]["supplemental_evidence"] = [
            item for item in result["instruments"][0]["supplemental_evidence"]
            if item["payload"]["kind"] != "financial"
        ]
    else:
        result["provider_qualifications"] = [
            item for item in result["provider_qualifications"] if item["provider"] == "eodhd"
        ]
    providers._official_financial_documents(ctx, result)
    assert result["instruments"] == []
    assert not providers._provider_stage_complete(ctx, result)
    assert "OFFICIAL_FINANCIAL_DOCUMENT_QUALIFICATION_REQUIRED" in {
        item["code"] for item in ctx.blockers
    }


def test_missing_official_issuer_identity_remains_unresolved_without_ch_demands(ctx):
    manifest = official_manifest()
    instrument = manifest.instruments[0]
    row = {
        "qualification_state": "UNRESOLVED_PROVIDER_MAPPING",
        "observed_at": NOW.isoformat(),
        "valid_until": (NOW + timedelta(hours=1)).isoformat(),
        "isin": instrument.identifiers.isin,
        "name": instrument.identifiers.company_name,
        "quote_currency": "GBX",
        "identifiers": instrument.identifiers.model_dump(mode="json"),
        "issuer_facts": {"CountryISO": "GB"},
        "companies_house_state": "UNRESOLVED",
    }
    result = universe.classify_row(ctx, row, {}, {})
    assert result is None
    assert "AUTHORITATIVE_ISSUER_IDENTITY_AND_JURISDICTION_REQUIRED" in row["reasons"]
    assert "ISSUER_ETHICAL_SCREENING_REQUIRED" in row["reasons"]
    assert not any("COMPANIES-HOUSE" in reason for reason in row["reasons"])
    assert "VERIFIED_ISSUER_JURISDICTION_AND_COMPANY_IDENTITY_REQUIRED" not in row["reasons"]


def test_provider_stage_policy_mismatch_cannot_reach_snapshot(ctx, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Source mismatch must stop before instrument acquisition")

    monkeypatch.setattr(snapshot, "_instrument_snapshot", forbidden)
    with pytest.raises(ValueError, match="SNAPSHOT_ISSUER_SOURCE_POLICY_MISMATCH"):
        snapshot.run_snapshot_stage(ctx, {
            "issuer_source_policy": "companies_house",
            "instruments": [], "provider_qualifications": [],
        })


def test_instrument_boundary_rejects_source_policy_mismatch(ctx, monkeypatch):
    manifest = official_manifest()
    sources = snapshot.QualificationSources(
        reviewed_instruments=manifest.instruments,
        provider_qualifications=manifest.provider_qualifications,
        issuer_source_policy="companies_house",
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Source mismatch must stop before snapshot database or fetch")

    monkeypatch.setattr(snapshot, "ResearchStore", forbidden)
    with pytest.raises(ValueError, match="SNAPSHOT_ISSUER_SOURCE_POLICY_MISMATCH"):
        snapshot._instrument_snapshot(ctx, sources, manifest.instruments[0])


def test_snapshot_cache_does_not_reuse_a_payload_from_other_source_policy(ctx, monkeypatch):
    prepare_snapshot_builder(monkeypatch)
    manifest = official_manifest()
    sources = snapshot.QualificationSources(
        reviewed_instruments=manifest.instruments,
        provider_qualifications=manifest.provider_qualifications,
        issuer_source_policy="official_disclosures",
    )
    fresh = live.LiveSnapshotBuilder(manifest, None, qlib_enabled=False)(
        manifest.instruments[0].metadata
    )
    mismatched = ResearchSnapshot.model_validate(fresh.model_dump() | {
        "issuer_source_policy": "companies_house", "hash": "",
    })
    wrong_reference = ctx.artifact(mismatched.model_dump(mode="json"))
    monkeypatch.setattr(ctx, "cache", lambda *args: {"snapshot_artifact": wrong_reference})
    acquisitions = []

    def builder(*args, **kwargs):
        def build(instrument):
            acquisitions.append(instrument.ticker)
            return fresh
        return build

    monkeypatch.setattr(snapshot, "LiveSnapshotBuilder", builder)
    with pytest.raises(ValueError, match="SNAPSHOT_ISSUER_SOURCE_POLICY_MISMATCH"):
        snapshot._instrument_snapshot(ctx, sources, manifest.instruments[0])
    assert acquisitions == []
