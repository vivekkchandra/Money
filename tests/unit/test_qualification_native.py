"""Synthetic seams for runner behavior, never production qualification evidence."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from money.qualification import native
from money.qualification.core import CommandResult, QualificationContext
from money.research.call_telemetry import InferenceReceipt, emit_calls


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    return QualificationContext(
        tmp_path / "bundle",
        Path(__file__).resolve().parents[2],
        {"OPENAI_API_KEY": "unit-test-only-secret-not-real"},
        datetime.now(UTC),
    )


def scripted_probe(monkeypatch: pytest.MonkeyPatch) -> list:
    calls = []

    def complete(self, system, user):
        calls.append(self.configuration)
        emit_calls(
            (
                InferenceReceipt(
                    provider="openai",
                    model=self.model,
                    duration_seconds=0.1,
                    status="SUCCEEDED",
                    input_tokens=12,
                    output_tokens=1,
                ),
            )
        )
        return "READY"

    monkeypatch.setattr(native.HTTPInference, "complete", complete)
    return calls


def approve(ctx: QualificationContext) -> None:
    review = ctx.read_json("reviews/inference.json")
    review.update(
        reviewed_by="independent-operator",
        reviewed_at=ctx.now.isoformat(),
        expires_at=(ctx.now + timedelta(days=1)).isoformat(),
        native_timeout_seconds=600,
        native_max_calls=16,
        budgets={
            "per_job": 10_000_000,
            "per_candidate": 10_000_000,
            "per_stage": 10_000_000,
            "per_agent": 10_000_000,
            "daily": 10_000_000,
            "per_model": [],
        },
    )
    ctx.write_json("reviews/inference.json", review)


def test_probe_all_exact_selections_without_persisting_key(ctx, monkeypatch):
    calls = scripted_probe(monkeypatch)
    result = native.run_inference_stage(ctx)
    assert len(calls) == 3
    assert all(call.model == "gpt-5.4-2026-03-05" for call in calls)
    assert all(
        call.max_output_tokens <= 512 and call.maximum_prompt_bytes <= 1000 for call in calls
    )
    assert result["complete"] is False  # access is not administrative approval
    assert len(result["artifacts"]) == 3
    for path in ctx.root.rglob("*"):
        if path.is_file():
            assert ctx.environ["OPENAI_API_KEY"].encode() not in path.read_bytes()


def test_resume_preserves_operator_input_and_reuses_valid_receipts(ctx, monkeypatch):
    calls = scripted_probe(monkeypatch)
    native.run_inference_stage(ctx)
    approve(ctx)
    reviewed_bytes = ctx.read_bytes("reviews/inference.json")
    result = native.run_inference_stage(ctx)
    assert result["complete"] is True and len(calls) == 3
    assert ctx.read_bytes("reviews/inference.json") == reviewed_bytes
    assert result["manifest_fields"]["tradingagents"]["input_gbp_per_million"] is None
    assert result["manifest_fields"]["native_max_calls"] == 16


def test_receipt_expiry_repeats_real_probe(ctx, monkeypatch):
    calls = scripted_probe(monkeypatch)
    native.run_inference_stage(ctx)
    ctx.now += timedelta(hours=2)
    native.run_inference_stage(ctx)
    assert len(calls) == 6


def test_receipt_corruption_repeats_affected_probe(ctx, monkeypatch):
    calls = scripted_probe(monkeypatch)
    result = native.run_inference_stage(ctx)
    ctx.write_json(result["artifacts"][0][1], {"tampered": True})
    # Exact receipt content and artifact identity are no longer usable.
    ctx.now += timedelta(seconds=1)
    native.run_inference_stage(ctx)
    assert len(calls) == 4


def test_missing_credential_never_uses_cached_success(ctx, monkeypatch):
    calls = scripted_probe(monkeypatch)
    native.run_inference_stage(ctx)
    ctx.environ = {}
    result = native.run_inference_stage(ctx)
    assert not result["complete"] and len(calls) == 3
    assert any(item["code"] == "OPENAI_CREDENTIAL_REQUIRED" for item in ctx.blockers)


def test_exception_and_provider_output_secrets_never_persist(ctx, monkeypatch):
    def failure(*args):
        raise RuntimeError(ctx.environ["OPENAI_API_KEY"])

    monkeypatch.setattr(native.HTTPInference, "complete", failure)
    result = native.run_inference_stage(ctx)
    assert not result["complete"] and not result["artifacts"]
    assert "unit-test-only-secret-not-real" not in json.dumps(ctx.blockers)


def test_wrong_response_cannot_become_access_receipt(ctx, monkeypatch):
    monkeypatch.setattr(native.HTTPInference, "complete", lambda *args: "Other model")
    result = native.run_inference_stage(ctx)
    assert not result["artifacts"]
    assert all("INFERENCE_PROBE_FAILED" in item["code"] for item in ctx.blockers[:3])


def test_unknown_rates_are_not_replaced_by_zero_and_unreviewed_rates_reject(ctx, monkeypatch):
    scripted_probe(monkeypatch)
    native.run_inference_stage(ctx)
    approve(ctx)
    review = ctx.read_json("reviews/inference.json")
    review["rates"] = {"crewai": ["1", "2"]}
    ctx.write_json("reviews/inference.json", review)
    assert native.run_inference_stage(ctx)["complete"] is False


def test_underfunded_workflow_is_not_silently_truncated(ctx, monkeypatch):
    scripted_probe(monkeypatch)
    native.run_inference_stage(ctx)
    approve(ctx)
    review = ctx.read_json("reviews/inference.json")
    review["budgets"]["per_job"] = 100
    ctx.write_json("reviews/inference.json", review)
    assert native.run_inference_stage(ctx)["complete"] is False
    assert ctx.read_json("reviews/inference.json")["native_max_calls"] == 16


@pytest.mark.parametrize(
    "change",
    [
        {"endpoint": "https://attacker.example/v1/chat/completions"},
        {"model": "gpt-5.4"},
        {"credential_environment_variable": "EODHD_API_KEY"},
    ],
)
def test_endpoint_and_credential_routing_cannot_be_redirected(ctx, tmp_path, change):
    source = json.loads((ctx.repo / "data/configuration/live-inference.json").read_bytes())
    source["tradingagents"].update(change)
    repository = tmp_path / "repo"
    target = repository / "data/configuration/live-inference.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(source))
    ctx.repo = repository
    with pytest.raises(ValueError, match="INFERENCE_ENDPOINT_OR_EXACT_MODEL_UNREVIEWED"):
        native.run_inference_stage(ctx)


@pytest.mark.parametrize(
    "audit_exit,rows,expected",
    [
        (0, [{"name": "chromadb", "version": "1.1.1", "vulns": []}], True),
        (0, [], False),
        (0, [{"name": "chromadb", "version": "1.1.1", "skip_reason": "unknown"}], False),
        (1, [{"name": "chromadb", "version": "1.1.1", "vulns": [{"id": "GHSA-test-only"}]}], False),
    ],
)
def test_security_requires_complete_unsuppressed_audit(
    ctx, monkeypatch, audit_exit, rows, expected
):
    commands = []

    def command(context, args, timeout=240):
        commands.append(args)
        if args[:3] == ["uv", "pip", "check"]:
            return CommandResult(0, b"All packages compatible")
        return CommandResult(audit_exit, json.dumps({"dependencies": rows}).encode())

    monkeypatch.setattr(native, "_command", command)
    result = native._security(ctx, [("chromadb", "1.1.1")])
    assert result["passed"] is expected
    assert "--ignore-vuln" not in commands[1]
    assert "--strict" in commands[1]
    if audit_exit:
        assert result["findings"][0]["advisories"] == ["GHSA-test-only"]


def test_preflight_source_mismatch_and_chroma_cannot_be_qualified(ctx, monkeypatch):
    monkeypatch.setattr(native, "_inventory", lambda: [("chromadb", "1.1.1")])

    def missing(package):
        raise ValueError("unqualified")

    monkeypatch.setattr(native, "require_pinned_source", missing)
    monkeypatch.setattr(
        native,
        "_security",
        lambda *args: {
            "passed": False,
            "findings": [{"name": "chromadb", "advisories": ["GHSA-test-only"]}],
            "artifacts": [],
        },
    )
    monkeypatch.setattr(
        native, "_resolve_native_closure", lambda *args: {"resolved": False, "artifacts": []}
    )
    result = native.run_native_preflight_stage(ctx)
    codes = {item["code"] for item in ctx.blockers}
    assert not result["complete"]
    assert "CHROMADB_SECURITY_ADVISORIES" in codes
    assert "HOST_EGRESS_TARGET_EVIDENCE_REQUIRED" in codes
    assert "NATIVE_SOURCE_UNQUALIFIED_CREWAI" in codes
    assert not (ctx.root / "manifest.json").exists()


def test_python_guard_or_operator_boolean_is_never_os_egress_proof(ctx):
    ctx.write_json("reviews/native-egress.json", {"native_egress_policy_verified": True})
    result = native._egress(ctx, "1" * 64, ("api.openai.com",))
    assert result["complete"] is False and not result["manifest_fields"]


def test_no_native_calls_without_prerequisites(ctx, monkeypatch):
    def forbidden(*args):
        raise AssertionError("Must not make paid native calls")

    monkeypatch.setattr(native.HTTPInference, "complete", forbidden)
    assert native.run_first_pass_stage(ctx)["complete"] is False
    assert native.run_cio_stage(ctx)["complete"] is False


def nft_policy(address="104.18.7.192", policy="drop"):
    return {
        "nftables": [
            {"table": {"family": "inet", "name": "money"}},
            {
                "chain": {
                    "family": "inet",
                    "table": "money",
                    "name": "output",
                    "type": "filter",
                    "hook": "output",
                    "policy": policy,
                }
            },
            {
                "rule": {
                    "family": "inet",
                    "table": "money",
                    "chain": "output",
                    "expr": [
                        {
                            "match": {
                                "op": "==",
                                "left": {"meta": {"key": "l4proto"}},
                                "right": "tcp",
                            }
                        },
                        {
                            "match": {
                                "op": "==",
                                "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                "right": 443,
                            }
                        },
                        {
                            "match": {
                                "op": "==",
                                "left": {"payload": {"protocol": "ip", "field": "daddr"}},
                                "right": address,
                            }
                        },
                        {"accept": None},
                    ],
                }
            },
        ]
    }


def test_nft_only_supports_strict_inet_default_drop_exact_inference_ips():
    policy = nft_policy()
    native._strict_nft_policy(json.dumps(policy).encode(), {"104.18.7.192"})
    with pytest.raises(ValueError):
        native._strict_nft_policy(
            json.dumps(nft_policy(policy="accept")).encode(), {"104.18.7.192"}
        )
    with pytest.raises(ValueError):
        native._strict_nft_policy(
            json.dumps(nft_policy(address="8.8.8.8")).encode(), {"104.18.7.192"}
        )


@pytest.mark.parametrize(
    "left,right",
    [
        ({"meta": {"key": "l4proto"}}, "udp"),
        ({"payload": {"protocol": "tcp", "field": "dport"}}, 53),
        ({"ct": {"key": "state"}}, "established"),
    ],
)
def test_nft_rejects_dns_datagram_or_stateful_escape(left, right):
    policy = nft_policy()
    policy["nftables"][2]["rule"]["expr"][0] = {"match": {"op": "==", "left": left, "right": right}}
    with pytest.raises(ValueError):
        native._strict_nft_policy(json.dumps(policy).encode(), {"104.18.7.192"})


def test_railway_env_on_mac_is_not_target_worker_identity(monkeypatch):
    monkeypatch.setattr(native.platform, "system", lambda: "Darwin")
    monkeypatch.setenv("RAILWAY_SERVICE_ID", "test-service-id")
    assert native._target_identity() is None


def test_resolution_attempt_uses_all_upstream_metadata_without_installing(ctx, monkeypatch):
    commands = []

    def conflict(context, args, timeout=240):
        commands.append(args)
        return CommandResult(
            1, b"No solution found when resolving dependencies: numpy constraints incompatible"
        )

    monkeypatch.setattr(native, "_command", conflict)
    result = native._resolve_native_closure(ctx)
    assert result["resolved"] is False and result["installs_performed"] is False
    assert commands[0][:3] == ["uv", "pip", "compile"]
    assert "--no-deps" not in commands[0] and "--overrides" not in commands[0]
    assert "--no-build" in commands[0] and "--python-platform" in commands[0]
    assert "upstreams/ai-hedge-fund/pyproject.toml" in result["input_sha256"]
    assert native._resolve_native_closure(ctx) == result and len(commands) == 1


def test_active_model_checks_registry_again_at_execution_time(ctx, monkeypatch):
    from types import SimpleNamespace

    model = object()
    disposed = []
    queried = []
    monkeypatch.setattr(native, "load_qualified_model", lambda *args: model)
    monkeypatch.setattr(
        native,
        "ResearchStore",
        lambda *args, **kwargs: SimpleNamespace(
            engine=SimpleNamespace(dispose=lambda: disposed.append(True))
        ),
    )

    class Registry:
        def __init__(self, store):
            pass

        def load_active(self, registry_id, artifact_hash, now):
            queried.append((registry_id, artifact_hash, now))
            raise ValueError("WITHDRAWN")

    monkeypatch.setattr(native, "ModelRegistry", Registry)
    with pytest.raises(ValueError, match="WITHDRAWN"):
        native._active_model(
            ctx,
            {
                "model_path": "/test-only/model",
                "qlib_registry_id": "test-model:v1",
                "qlib_artifact_hash": "test-only-hash",
            },
        )
    assert queried == [("test-model:v1", "test-only-hash", ctx.now)]
    assert disposed == [True]


def test_dependency_cache_value_cannot_promote_real_resolver_conflict(ctx, monkeypatch):
    calls = []

    def conflict(context, arguments, timeout=240):
        calls.append(arguments)
        return CommandResult(1, b"No solution found: incompatible pinned numpy constraints")

    monkeypatch.setattr(native, "_command", conflict)
    original = native._resolve_native_closure(ctx)
    checkpoint = ctx.read_json("state/native-dependency-resolution.json")
    checkpoint["value"]["resolved"] = True
    checkpoint["value"]["exit_code"] = 0
    ctx.write_json("state/native-dependency-resolution.json", checkpoint)
    observed = native._resolve_native_closure(ctx)
    assert observed["resolved"] is False and observed["exit_code"] == 1
    assert observed["artifacts"] == original["artifacts"]
    assert len(calls) == 1


def test_security_cache_flags_are_reconstructed_from_actual_advisory_report(ctx, monkeypatch):
    inventory = [("chromadb", "1.1.1")]
    requirements = ctx.artifact(b"chromadb==1.1.1\n")
    evidence = ctx.artifact(
        {
            "kind": "money-native-security-v1",
            "verified_at": ctx.now.isoformat(),
            "inventory": inventory,
            "dependency_check_exit": 0,
            "audit_exit": 1,
            "security_warnings_ignored": False,
            "passed": False,
            "audit": {
                "dependencies": [
                    {
                        "name": "chromadb",
                        "version": "1.1.1",
                        "vulns": [{"id": "GHSA-synthetic-regression"}],
                    }
                ]
            },
        }
    )
    refs = (requirements, evidence)
    ctx.checkpoint(
        "native-security",
        native.content_hash(inventory),
        {"passed": True, "findings": [], "artifacts": refs},
        artifacts=refs,
    )

    def unexpected(*args):
        raise AssertionError("Actual hash-verified failure must remain a failure")

    monkeypatch.setattr(native, "_command", unexpected)
    result = native._security(ctx, inventory)
    assert result["passed"] is False
    assert result["findings"][0]["advisories"] == ["GHSA-synthetic-regression"]


def test_security_cache_cannot_reuse_a_different_dependency_inventory(ctx, monkeypatch):
    inventory = [("chromadb", "1.1.1")]
    requirements = ctx.artifact(b"chromadb==1.1.1\n")
    evidence = ctx.artifact(
        {
            "kind": "money-native-security-v1",
            "verified_at": ctx.now.isoformat(),
            "inventory": [["different-package", "1.0"]],
            "dependency_check_exit": 0,
            "audit_exit": 0,
            "security_warnings_ignored": False,
            "passed": True,
            "audit": {
                "dependencies": [{"name": "different-package", "version": "1.0", "vulns": []}]
            },
        }
    )
    refs = (requirements, evidence)
    ctx.checkpoint(
        "native-security",
        native.content_hash(inventory),
        {"passed": True, "findings": [], "artifacts": refs},
        artifacts=refs,
    )
    attempts = []

    def unavailable(*args):
        attempts.append(True)
        raise ValueError("ACTUAL_AUDIT_UNAVAILABLE")

    monkeypatch.setattr(native, "_command", unavailable)
    with pytest.raises(ValueError, match="ACTUAL_AUDIT_UNAVAILABLE"):
        native._security(ctx, inventory)
    assert attempts == [True]


def test_changed_outer_checkpoint_time_cannot_extend_inference_receipt(ctx, monkeypatch):
    calls = scripted_probe(monkeypatch)
    native.run_inference_stage(ctx)
    ctx.now += timedelta(hours=2)
    for firm in native.FIRMS:
        path = "state/inference-" + firm + ".json"
        checkpoint = ctx.read_json(path)
        checkpoint["created_at"] = ctx.now.isoformat()
        ctx.write_json(path, checkpoint)
    native.run_inference_stage(ctx)
    assert len(calls) == 6


def test_changed_outer_checkpoint_time_cannot_extend_resolver_observation(ctx, monkeypatch):
    calls = []

    def conflict(context, arguments, timeout=240):
        calls.append(arguments)
        return CommandResult(1, b"No solution found: pinned dependency conflict")

    monkeypatch.setattr(native, "_command", conflict)
    original = native._resolve_native_closure(ctx)
    ctx.now += timedelta(hours=2)
    checkpoint = ctx.read_json("state/native-dependency-resolution.json")
    checkpoint["created_at"] = ctx.now.isoformat()
    ctx.write_json("state/native-dependency-resolution.json", checkpoint)
    refreshed = native._resolve_native_closure(ctx)
    assert len(calls) == 2
    assert refreshed["observed_at"] != original["observed_at"]


def test_changed_outer_checkpoint_time_cannot_extend_actual_security_scan(ctx, monkeypatch):
    calls = []

    def command(context, arguments, timeout=240):
        calls.append(arguments)
        return CommandResult(
            0,
            json.dumps(
                {
                    "dependencies": [
                        {"name": "test-dependency", "version": "1.0", "vulns": []},
                    ]
                }
            ).encode(),
        )

    monkeypatch.setattr(native, "_command", command)
    inventory = [("test-dependency", "1.0")]
    assert native._security(ctx, inventory)["passed"] is True
    ctx.now += timedelta(hours=2)
    checkpoint = ctx.read_json("state/native-security.json")
    checkpoint["created_at"] = ctx.now.isoformat()
    ctx.write_json("state/native-security.json", checkpoint)
    assert native._security(ctx, inventory)["passed"] is True
    assert len(calls) == 4  # Dependency check and real advisory scan repeated.
