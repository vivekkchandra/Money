"""Offline diagnostics must not qualify credentials, contact providers or leak input."""

import json
from types import SimpleNamespace

import pytest

from money.research import preflight


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    # Remove task configuration without reading or displaying any existing values.
    for key in tuple(preflight.os.environ):
        if key.startswith(("MONEY_", "RESEARCH_", "EODHD_", "COMPANIES_HOUSE_")) or key == "DATABASE_URL":
            monkeypatch.delenv(key)


def test_missing_configuration_is_diagnostic_not_exception_or_fallback():
    result = preflight.deployment_preflight("worker")
    assert result["production_status"] == "PRODUCTION BLOCKED"
    assert not result["manifest_validated"] and not result["settings_validated"]
    assert any(check["code"] == "PINNED_QUALIFICATION_BUNDLE_REQUIRED" for check in result["checks"])
    assert any(check["name"] == "current_stock_universe" and check["status"] == "NOT_VERIFIED" for check in result["checks"])


def test_unsafe_modes_cannot_be_reported_as_target_configuration(monkeypatch):
    monkeypatch.setenv("MONEY_ENV", "development")
    monkeypatch.setenv("MONEY_RESEARCH_MODE", "live_rnd")
    monkeypatch.setenv("MONEY_ENABLE_SYNTHETIC_DEMO", "true")
    report = preflight.deployment_preflight("api")
    checks = {row["name"]: row for row in report["checks"]}
    for name in ("MONEY_ENV", "MONEY_RESEARCH_MODE", "MONEY_ENABLE_SYNTHETIC_DEMO"):
        assert checks[name]["status"] == "BLOCKED_CONFIGURATION"


def test_no_secret_path_or_exception_payload_in_output(monkeypatch):
    secret = "NEVER_SERIALIZE_PRIVATE_CREDENTIAL_VALUE"
    monkeypatch.setenv("RESEARCH_API_TOKEN", secret)
    monkeypatch.setenv("EODHD_API_KEY", secret)
    monkeypatch.setenv("MONEY_LIVE_MANIFEST", "/" + secret + "/manifest.json")
    monkeypatch.setenv("MONEY_LIVE_MANIFEST_SHA256", "a" * 64)

    def reject(*args, **kwargs):
        raise ValueError(secret)

    monkeypatch.setattr(preflight, "Settings", reject)
    monkeypatch.setattr(preflight, "load_manifest", reject)
    raw = json.dumps(preflight.deployment_preflight("worker"))
    assert secret not in raw
    assert "SETTINGS_VALIDATION_FAILED" in raw
    assert "QUALIFICATION_BUNDLE_INVALID_OR_EXPIRED" in raw


def test_even_successful_offline_schema_is_never_live_qualification(monkeypatch):
    # Dependency seams are synthetic TEST inputs only; no qualification artifact created.
    manifest = SimpleNamespace(
        provider_qualifications=(), market_credential_environment_variable="EODHD_API_KEY",
        filings_credential_environment_variable="COMPANIES_HOUSE_API_KEY",
        issuer_source_policy="companies_house",
        **{name: SimpleNamespace(credential_environment_variable="SYNTHETIC_TEST_KEY")
           for name in ("tradingagents", "ai_hedge_fund", "crewai")},
    )
    monkeypatch.setattr(preflight, "Settings", lambda: SimpleNamespace())
    monkeypatch.setattr(preflight, "load_manifest", lambda *_: manifest)
    monkeypatch.setenv("MONEY_LIVE_MANIFEST", "fixture-only")
    monkeypatch.setenv("MONEY_LIVE_MANIFEST_SHA256", "a" * 64)
    monkeypatch.setenv("SYNTHETIC_TEST_KEY", "fixture-only")
    report = preflight.deployment_preflight("worker")
    assert report["manifest_validated"] and report["settings_validated"]
    assert report["production_status"] == "PRODUCTION BLOCKED"
    assert not any(row["status"] == "READY" for row in report["checks"])
    assert any(row["name"] == "crewai_credential" and row["status"] == "PRESENT_NOT_TESTED" for row in report["checks"])


def test_api_preflight_does_not_demand_inference_secrets():
    report = preflight.deployment_preflight("api")
    assert not any(row["name"].endswith("_credential") for row in report["checks"])


def test_official_disclosures_need_evidence_not_companies_house_credential(monkeypatch):
    monkeypatch.setenv("MONEY_ISSUER_SOURCE_POLICY", "official_disclosures")
    monkeypatch.setenv("MONEY_QLIB_ENABLED", "false")
    report = preflight.deployment_preflight("worker")
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["filings_credential"]["status"] == "NOT_REQUIRED"
    assert checks["filings_credential"]["code"] == "OFFICIAL_DISCLOSURES_SELECTED_EVIDENCE_STILL_REQUIRED"
    assert checks["market_credential"]["status"] == "BLOCKED_CREDENTIAL"
    assert checks["promoted_qlib_model"]["status"] == "NOT_REQUIRED"
    assert checks["lean"]["status"] == "NOT_VERIFIED"
    assert report["production_status"] == "PRODUCTION BLOCKED"


def test_cyclic_manifest_parent_is_blocked_without_leaking_path(tmp_path, monkeypatch):
    (tmp_path / "first").symlink_to(tmp_path / "second", target_is_directory=True)
    (tmp_path / "second").symlink_to(tmp_path / "first", target_is_directory=True)
    monkeypatch.setenv("MONEY_LIVE_MANIFEST", str(tmp_path / "first" / "manifest.json"))
    monkeypatch.setenv("MONEY_LIVE_MANIFEST_SHA256", "a" * 64)
    report = preflight.deployment_preflight("api")
    raw = json.dumps(report)
    assert str(tmp_path) not in raw
    assert "QUALIFICATION_BUNDLE_INVALID_OR_EXPIRED" in raw
    assert not report["manifest_validated"]


def test_cli_never_returns_release_success(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["preflight", "--role", "api"])
    with pytest.raises(SystemExit) as failure:
        preflight.main()
    assert failure.value.code == 2
    assert json.loads(capsys.readouterr().out)["production_status"] == "PRODUCTION BLOCKED"
