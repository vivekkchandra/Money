"""Synthetic mechanics, not provider permission or live qualification evidence."""

import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from money.api.settings import Settings
from money.data.qualification import ProviderQualification
from money.qualification.core import QualificationContext
from money.qualification.runner import assemble_manifest
from money.research.live import LiveManifest
from money.usage_policy import PersonalUseAudit, UsageMode, usage_mode

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def personal_audit(**updates):
    return PersonalUseAudit(
        **{
            "provider": "eodhd",
            "datasets": ("ohlcv", "corporate_action", "news"),
            "endpoints": ("https://eodhd.com/api/eod/FIXTURE.LSE",),
            "attribution": "EODHD; synthetic fixture only",
            **updates,
        }
    )


def qualification(**updates):
    return ProviderQualification(
        **{
            "provider": "eodhd",
            "datasets": ("ohlcv", "corporate_action", "news"),
            "earliest_observation": NOW - timedelta(days=365),
            "publication_times": "AS_RETRIEVED",
            "maximum_age_seconds": 86400,
            "qualification_report_hash": hashlib.sha256(b"synthetic dataset test report").hexdigest(),
            "verified_at": NOW,
            "valid_until": NOW + timedelta(hours=2),
            "redistribution": "PROHIBITED",
            "attribution": "Synthetic fixture attribution",
            "source_documentation": "https://example.test/provider-terms",
            "personal_use": personal_audit(),
            **updates,
        }
    )


def test_personal_opt_in_is_explicit_and_production_is_default():
    assert usage_mode({}) == UsageMode.HOSTED_COMMERCIAL_PRODUCTION
    assert usage_mode({"MONEY_ENV": "development"}) == UsageMode.HOSTED_COMMERCIAL_PRODUCTION
    assert usage_mode({"MONEY_USAGE_MODE": "personal_research"}) == UsageMode.PERSONAL_RESEARCH
    with pytest.raises(ValueError, match="MONEY_USAGE_MODE_INVALID"):
        usage_mode({"MONEY_USAGE_MODE": "unknown"})


def test_personal_unsigned_rights_remain_unverified_and_do_not_manufacture_review():
    result = qualification()
    result.require("ohlcv", NOW, usage_mode=UsageMode.PERSONAL_RESEARCH)
    assert result.personal_use.rights_status == "UNVERIFIED_PERSONAL_USE"
    assert result.production_qualified is False
    assert result.qualified_by is None
    assert "reviewed_at" not in result.personal_use.model_dump()


def test_ambient_personal_mode_never_changes_production_call_default(monkeypatch):
    monkeypatch.setenv("MONEY_USAGE_MODE", "personal_research")
    with pytest.raises(ValueError, match="PROVIDER_UNQUALIFIED"):
        qualification().require("ohlcv", NOW)


@pytest.mark.parametrize("update,error", [
    ({"qualification_report_hash": None}, "PROVIDER_UNQUALIFIED"),
    ({"development_only": True}, "PROVIDER_UNQUALIFIED"),
    ({"datasets": ("news",)}, "PROVIDER_COVERAGE_MISSING"),
    ({"valid_until": NOW}, "PROVIDER_COVERAGE_MISSING"),
    ({"verified_at": NOW + timedelta(seconds=1)}, "PROVIDER_COVERAGE_MISSING"),
    ({"production_qualified": True}, "PERSONAL_USE_CANNOT_CLAIM_PRODUCTION_RIGHTS"),
    ({"redistribution": "LICENSED"}, "PERSONAL_USE_CANNOT_CLAIM_PRODUCTION_RIGHTS"),
])
def test_personal_keeps_dataset_integrity_freshness_and_release_checks(update, error):
    with pytest.raises(ValueError, match=error):
        qualification(**update).require("ohlcv", NOW, usage_mode=UsageMode.PERSONAL_RESEARCH)


def test_personal_does_not_qualify_original_publication_history():
    with pytest.raises(ValueError, match="PROVIDER_HISTORICAL_AVAILABILITY_UNKNOWN"):
        qualification().require("ohlcv", NOW, historical=True, usage_mode=UsageMode.PERSONAL_RESEARCH)


def test_personal_audit_requires_exact_provider_and_dataset():
    with pytest.raises(ValueError, match="PERSONAL_USE_AUDIT_COVERAGE_MISSING"):
        qualification(personal_use=personal_audit(provider="other")).require(
            "ohlcv", NOW, usage_mode=UsageMode.PERSONAL_RESEARCH
        )


@pytest.mark.parametrize("field", ["redistribution", "public_raw_display", "resale", "external_sharing"])
def test_personal_cannot_enable_redistribution_or_public_raw_display(field):
    with pytest.raises(ValidationError):
        personal_audit(**{field: True})
    with pytest.raises(ValueError, match="PERSONAL_USE_PUBLIC_COMMERCIAL_REDISTRIBUTION_FORBIDDEN"):
        personal_audit().require_release()


def test_personal_audit_cannot_record_credential_urls():
    with pytest.raises(ValueError, match="MUST_BE_SECRET_FREE"):
        personal_audit(endpoints=("https://example.test/api?api_token=fixture-not-a-key",))


def test_reviewed_commercial_qualification_still_works():
    result = qualification(personal_use=None, production_qualified=True, qualified_by="test-reviewer")
    result.require("ohlcv", NOW)
    result.require("ohlcv", NOW, usage_mode=UsageMode.PERSONAL_RESEARCH)
    assert "personal_use" not in result.model_dump()


def test_personal_bundle_cannot_create_production_manifest(tmp_path):
    ctx = QualificationContext(tmp_path, tmp_path, {"MONEY_USAGE_MODE": "personal_research"}, NOW)
    assert assemble_manifest(ctx, []) is None
    assert {item["code"] for item in ctx.blockers} == {"PERSONAL_RESEARCH_COMMERCIAL_RELEASE_FORBIDDEN"}
    assert not (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "reviews/release.json").exists()


def test_manifest_rejects_personal_provider_even_if_production_flag_forged():
    from test_filing_documents import reviewed_manifest

    value = reviewed_manifest().model_dump()
    index = next(i for i, item in enumerate(value["provider_qualifications"]) if item["provider"] == "eodhd")
    value["provider_qualifications"][index]["personal_use"] = personal_audit().model_dump()
    with pytest.raises(ValueError, match="LIVE_MANIFEST_PERSONAL_USE_FORBIDDEN"):
        LiveManifest.model_validate(value)


def test_personal_mode_cannot_enable_hosted_api():
    with pytest.raises(ValueError, match="Personal research is forbidden"):
        Settings(
            money_usage_mode="personal_research",
            money_env="production",
            money_deployment_env="hosted",
            database_url="postgresql://fixture/fixture",
        )


def test_personal_snapshot_scope_is_frozen_and_does_not_change_legacy_hashes():
    from test_universe_snapshot import review, snapshot

    from money.schemas.contracts import ResearchSnapshot

    original = snapshot(review())
    assert "usage_mode" not in original.model_dump()
    assert ResearchSnapshot.model_validate(original.model_dump()).hash == original.hash
    personal = ResearchSnapshot.model_validate({
        **original.model_dump(), "usage_mode": "PERSONAL_RESEARCH", "hash": "", "qlib_enabled": False
    })
    assert personal.hash != original.hash
    assert personal.required_first_pass_firms == frozenset({"tradingagents", "ai_hedge_fund"})
    with pytest.raises(ValueError, match="PERSONAL_USE_PUBLIC_COMMERCIAL_REDISTRIBUTION_FORBIDDEN"):
        personal.require_commercial_release()
    with pytest.raises(ValueError, match="snapshot hash mismatch"):
        ResearchSnapshot.model_validate({**personal.model_dump(), "usage_mode": "HOSTED_COMMERCIAL_PRODUCTION"})


def test_personal_native_inference_rejects_remote_and_unknown_transports_before_calls():
    from money.adapters.native import InferenceSession, NativeRunSettings
    from money.research.inference import HTTPInference, InferenceConfiguration

    remote = HTTPInference(InferenceConfiguration(
        provider="fixture", model="fixture", endpoint="https://example.test/v1/chat/completions",
        api_key="synthetic-unit-test-only",
    ))
    for inference in (remote, object()):
        with pytest.raises(ValueError, match="PERSONAL_USE_REMOTE_INFERENCE_SHARING_FORBIDDEN"):
            InferenceSession(inference, NativeRunSettings(), usage_mode="PERSONAL_RESEARCH")


def test_personal_native_inference_requires_actual_loopback_transport_and_rechecks_mutation():
    from money.adapters.native import InferenceSession, NativeRunSettings
    from money.research.inference import HTTPInference, InferenceConfiguration

    local = HTTPInference(InferenceConfiguration(
        provider="ollama", model="qwen3:14b", endpoint="http://127.0.0.1:11434/v1/chat/completions",
        api_key=None, authentication="none", endpoint_scope="local",
    ))
    session = InferenceSession(local, NativeRunSettings(), usage_mode="PERSONAL_RESEARCH")
    local.configuration = InferenceConfiguration(
        provider="fixture", model="fixture", endpoint="https://example.test/v1/chat/completions",
        api_key="synthetic-unit-test-only",
    )
    with pytest.raises(ValueError, match="PERSONAL_USE_REMOTE_INFERENCE_SHARING_FORBIDDEN"):
        session.complete("unit test", "must not leave local workflow")
    assert session.calls == 0
