"""Local inference functionality is never production research admission."""

from pathlib import Path

import pytest

from money.api.settings import Settings
from money.research.inference_config import InferenceSelection, load_inference_selections
from money.research.live import LiveManifest

REPO = Path(__file__).resolve().parents[2]
LOCAL_CONFIG = "data/configuration/ollama-inference.json"


def test_legacy_selection_import_remains_the_same_validated_contract():
    from money.research.live import InferenceSelection as LegacySelection

    assert LegacySelection is InferenceSelection


@pytest.mark.parametrize("role", ["tradingagents", "ai_hedge_fund", "crewai"])
def test_live_manifest_cannot_admit_local_inference_even_with_other_proofs(role):
    from test_filing_documents import reviewed_manifest

    fields = reviewed_manifest().model_dump(mode="json")
    local = load_inference_selections(REPO, {"MONEY_INFERENCE_CONFIG": LOCAL_CONFIG})
    fields[role] = local[role].model_dump(mode="json")
    with pytest.raises(ValueError, match="HOSTED_LOCAL_INFERENCE_DENIED"):
        LiveManifest.model_validate(fields)


@pytest.mark.parametrize(
    "environment,deployment",
    [
        ("production", "hosted"),
        ("preview", "hosted"),
        ("development", "hosted"),
        ("production", "local"),
    ],
)
def test_nonlocal_settings_reject_ollama_before_manifest_or_credential_access(
    environment, deployment
):
    with pytest.raises(ValueError, match="HOSTED_LOCAL_INFERENCE_DENIED"):
        Settings(
            money_env=environment,
            money_deployment_env=deployment,
            money_research_mode="live",
            money_inference_config=LOCAL_CONFIG,
            database_url="postgresql://unused/fixture",
        )


def test_local_configuration_does_not_require_any_inference_credential():
    settings = Settings(
        money_env="development",
        money_deployment_env="local",
        money_research_mode="unconfigured",
        money_inference_config=LOCAL_CONFIG,
        database_url="sqlite:///:memory:",
        money_allow_unauthenticated_dev=True,
    )
    assert settings.money_inference_config == Path(LOCAL_CONFIG)
    assert not settings.money_enable_synthetic_demo


def test_local_inference_does_not_remove_the_live_manifest_gate():
    with pytest.raises(ValueError, match="Live research requires a pinned qualification manifest"):
        Settings(
            money_env="development",
            money_deployment_env="local",
            money_research_mode="live",
            money_inference_config=LOCAL_CONFIG,
            database_url="sqlite:///:memory:",
            money_allow_unauthenticated_dev=True,
        )


def test_public_override_must_match_the_admitted_manifest(tmp_path, monkeypatch):
    from test_filing_documents import reviewed_manifest

    manifest = reviewed_manifest()
    path = tmp_path / "manifest.json"
    path.write_bytes(b"unit-test-placeholder-never-production-evidence")
    monkeypatch.setattr("money.research.live.load_manifest", lambda *_: manifest)
    with pytest.raises(ValueError, match="INFERENCE_CONFIG_MANIFEST_MISMATCH"):
        Settings(
            money_env="production",
            money_deployment_env="hosted",
            money_research_mode="live",
            money_inference_config="data/configuration/live-inference.json",
            money_live_manifest=path,
            money_live_manifest_sha256="a" * 64,
            database_url="postgresql://unused/fixture",
        )
