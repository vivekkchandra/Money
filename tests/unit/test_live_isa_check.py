"""Offline synthetic inputs test selection safety, not live broker eligibility."""

import json
import runpy
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_instrument_catalogue import NOW, fixture_entry, fixture_qualification

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_live_isa.py"))
NAMESPACE = MODULE["main"].__globals__


def candidate(ticker="ACCEPTED.L", *, documents=False, **updates):
    entry = fixture_entry(**updates)
    entry = replace(
        entry,
        metadata=entry.metadata.model_copy(update={"ticker": ticker}),
        identifiers=entry.identifiers.model_copy(
            update={
                "ticker": ticker,
                "trading212_id": ticker + "_EQ",
                "exchange_ticker": ticker,
            }
        ),
    )
    return SimpleNamespace(
        metadata=entry.metadata,
        identifiers=entry.identifiers,
        eligibility_proof_hash="a" * 64,
        ethical_proof_hash="b" * 64,
        filing_documents=("synthetic-unit-proof",) if documents else (),
    )


def manifest(*instruments):
    return SimpleNamespace(
        instruments=instruments,
        provider_qualifications=(fixture_qualification(),),
    )


def test_selection_never_uses_first_invalid_entry_or_default_us_symbol():
    rejected = candidate("AAPL", quote_currency="USD")
    accepted = candidate("REALSHAPE.L")
    selected = MODULE["select_candidates"](manifest(rejected, accepted), NOW)
    assert selected == (accepted,)


@pytest.mark.parametrize(
    "updates",
    [
        {"quote_currency": "USD"},
        {"quote_currency": "EUR"},
        {"instrument_type": "ETF"},
        {"isa_available": None},
        {"isa_available": False},
        {"currently_available": False},
        {"activities_verified": False},
        {"business_activities": ("Defence",)},
        {"verified_at": NOW - timedelta(days=2)},
        {"verified_at": NOW + timedelta(days=1)},
    ],
)
def test_no_current_qualified_candidate_is_failure_not_missing_credential_skip(updates):
    with pytest.raises(MODULE["LiveAcceptanceFailure"]) as caught:
        MODULE["select_candidates"](manifest(candidate(**updates)), NOW)
    assert caught.value.status == "FAILED"
    assert caught.value.code == "NO_CURRENT_VERIFIED_ISA_CANDIDATE"


def test_native_snapshot_must_match_exact_eligible_reviewed_ticker():
    specification = manifest(candidate("MATCH.L"), candidate("OTHER.L"))
    assert (
        MODULE["select_candidates"](specification, NOW, ticker="OTHER.L")[0].metadata.ticker
        == "OTHER.L"
    )
    for ticker in ("MATCH", "UNKNOWN.L", "AAPL", "DEMO.L"):
        with pytest.raises(
            MODULE["LiveAcceptanceFailure"], match="NO_CURRENT_VERIFIED_ISA_CANDIDATE"
        ):
            MODULE["select_candidates"](specification, NOW, ticker=ticker)


def test_document_selection_is_also_filtered_by_current_isa_mandate():
    disallowed = candidate("EXCLUDED.L", documents=True, business_activities=("weapons",))
    no_documents = candidate("ALLOWED.L")
    allowed = candidate("QUALIFIED.L", documents=True)
    selected = MODULE["select_candidates"](
        manifest(disallowed, no_documents, allowed),
        NOW,
        require_filing_documents=True,
    )
    assert selected == (allowed,)
    with pytest.raises(MODULE["LiveAcceptanceFailure"], match="NO_CURRENT_VERIFIED_ISA_CANDIDATE"):
        MODULE["select_candidates"](
            manifest(disallowed, no_documents),
            NOW,
            require_filing_documents=True,
        )


def test_selection_is_deterministic_and_bounded():
    entries = (candidate("Z.L"), candidate("A.L"), candidate("M.L"))
    selected = MODULE["select_candidates"](manifest(*entries), NOW, limit=2)
    assert [item.metadata.ticker for item in selected] == ["A.L", "M.L"]
    for limit in (0, 4, 10000):
        with pytest.raises(MODULE["LiveAcceptanceFailure"], match="SELECTION_LIMIT_INVALID"):
            MODULE["select_candidates"](manifest(*entries), NOW, limit=limit)


def test_future_expired_or_unqualified_catalogue_fails_without_substitution():
    specification = manifest(candidate())
    specification.provider_qualifications = (
        fixture_qualification().model_copy(update={"production_qualified": False}),
    )
    with pytest.raises(MODULE["LiveAcceptanceFailure"], match="QUALIFIED_ISA_UNIVERSE_INVALID"):
        MODULE["select_candidates"](specification, NOW)


@pytest.mark.parametrize(
    "environment,code",
    [
        ({}, "LIVE_ACCEPTANCE_OPT_IN_REQUIRED"),
        ({"MONEY_RUN_PRODUCTION_INTEGRATION": "0"}, "LIVE_ACCEPTANCE_OPT_IN_REQUIRED"),
        ({"MONEY_RUN_PRODUCTION_INTEGRATION": "1"}, "QUALIFICATION_MANIFEST_REQUIRED"),
        (
            {"MONEY_RUN_PRODUCTION_INTEGRATION": "1", "MONEY_LIVE_MANIFEST": "fixture"},
            "QUALIFICATION_MANIFEST_REQUIRED",
        ),
    ],
)
def test_missing_configuration_is_explicit_skip_not_pass(environment, code, monkeypatch):
    monkeypatch.setitem(
        NAMESPACE, "load_manifest", lambda *_: pytest.fail("Manifest unexpectedly read")
    )
    with pytest.raises(MODULE["LiveAcceptanceFailure"]) as caught:
        MODULE["configured_manifest"](environment)
    assert caught.value.status == "SKIPPED_MISSING_CREDENTIAL"
    assert caught.value.code == code


def test_missing_configured_file_is_external_infrastructure_skip(tmp_path):
    with pytest.raises(MODULE["LiveAcceptanceFailure"]) as caught:
        MODULE["configured_manifest"](
            {
                "MONEY_RUN_PRODUCTION_INTEGRATION": "1",
                "MONEY_LIVE_MANIFEST": str(tmp_path / "missing"),
                "MONEY_LIVE_MANIFEST_SHA256": "a" * 64,
            }
        )
    assert caught.value.status == "BLOCKED_EXTERNAL_INFRA"
    assert str(tmp_path) not in str(caught.value)


def test_invalid_pinned_manifest_is_failure_and_never_exposes_payload(tmp_path):
    path = tmp_path / "invalid.json"
    path.write_text('{"private-test-payload":"must-not-be-output"}')
    with pytest.raises(MODULE["LiveAcceptanceFailure"]) as caught:
        MODULE["configured_manifest"](
            {
                "MONEY_RUN_PRODUCTION_INTEGRATION": "1",
                "MONEY_LIVE_MANIFEST": str(path),
                "MONEY_LIVE_MANIFEST_SHA256": "a" * 64,
            }
        )
    assert caught.value.status == "FAILED" and caught.value.code == "QUALIFICATION_MANIFEST_INVALID"
    assert "private-test-payload" not in str(caught.value)
    assert str(path) not in str(caught.value)


@pytest.mark.parametrize(
    "argv", [[], ["--select-only", "--limit", "4"], ["--select-only", "--ticker", "AAPL"]]
)
def test_cli_requires_selection_opt_in_and_has_no_arbitrary_ticker_override(argv):
    with pytest.raises(SystemExit) as caught:
        MODULE["main"](argv)
    assert caught.value.code == 2


def test_cli_reports_selection_only_never_live_research_pass(monkeypatch, capsys):
    monkeypatch.setitem(NAMESPACE, "configured_manifest", lambda: manifest(candidate()))
    monkeypatch.setitem(NAMESPACE, "utc_now", lambda: NOW)
    assert MODULE["main"](["--select-only"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "CANDIDATES_SELECTED"
    assert result["scope"] == "READ_ONLY_ACCEPTANCE_SELECTION"
    assert result["research_execution"] == "NOT_RUN"
    assert result["production_qualified"] is False
    assert result["complete_broker_universe"] is False
    assert result["candidates"][0]["ticker"] == "ACCEPTED.L"


def test_cli_missing_real_manifest_returns_nonzero_and_no_fabricated_candidate(monkeypatch, capsys):
    monkeypatch.setenv("MONEY_RUN_PRODUCTION_INTEGRATION", "1")
    monkeypatch.delenv("MONEY_LIVE_MANIFEST", raising=False)
    monkeypatch.delenv("MONEY_LIVE_MANIFEST_SHA256", raising=False)
    assert MODULE["main"](["--select-only"]) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "SKIPPED_MISSING_CREDENTIAL"
    assert result["reason"] == "QUALIFICATION_MANIFEST_REQUIRED"
    assert result["candidates"] == [] and result["research_execution"] == "NOT_RUN"


def test_cli_configured_empty_universe_fails_instead_of_skipping(monkeypatch, capsys):
    monkeypatch.setitem(
        NAMESPACE, "configured_manifest", lambda: manifest(candidate(isa_available=None))
    )
    monkeypatch.setitem(NAMESPACE, "utc_now", lambda: NOW)
    assert MODULE["main"](["--select-only"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "FAILED"
    assert result["reason"] == "NO_CURRENT_VERIFIED_ISA_CANDIDATE"
    assert result["candidates"] == [] and result["research_execution"] == "NOT_RUN"
