"""Policy migration fixtures are synthetic and confined to temporary roots."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from money.qualification.core import QualificationContext
from money.qualification.universe_policy import (
    DERIVED_PATHS,
    JOURNAL,
    MODE,
    REBUILD_SOURCE,
    UNIVERSE_POLICY_VERSION,
    ensure_universe_policy,
)


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    return QualificationContext(
        tmp_path / "bundle", tmp_path, {}, datetime(2026, 9, 17, tzinfo=UTC)
    )


def seed(ctx: QualificationContext, version: str | None) -> dict[str, bytes]:
    value: dict[str, Any] = {"reasons": ["VENUE_COUNTRY_NOT_VERIFIED"]}
    if version is not None:
        value["universe_policy_version"] = version
    for relative in DERIVED_PATHS:
        if relative.endswith(".csv"):
            ctx.write_bytes(relative, b"ticker,state\nSYNTHETIC,UNRESOLVED_IDENTITY\n")
        else:
            ctx.write_json(relative, value)
    return {relative: ctx.read_bytes(relative) or b"" for relative in DERIVED_PATHS}


@pytest.mark.parametrize("old_version", [None, "money-uk-venue-universe-v1", "future-policy-v3"])
def test_mismatch_archives_all_and_preserves_raw_inputs(
    ctx: QualificationContext, old_version: str | None
) -> None:
    previous = seed(ctx, old_version)
    preserved = {
        "artifacts/raw.json": b'{"actual_response":"synthetic fixture"}\n',
        "state/bulk-broker-metadata.json": b'{"status":"CURRENT","observed_at":"unchanged"}',
        "state/bulk-provider-cache/raw.json": b'{"receipt":"unchanged"}',
        "reviews/inference.json": b'{"review":"unsigned"}',
        "inputs/universe/account-scope.json": b'{"review":"unsigned"}',
        "outputs/native-preflight.json": b'{"status":"BLOCKED"}',
        "outputs/snapshot-result.json": b'{"status":"BLOCKED"}',
        "manifest.json": b'{"fixture":"never rewritten"}',
    }
    for relative, raw in preserved.items():
        ctx.write_bytes(relative, raw)

    result = ensure_universe_policy(ctx)

    assert result["rebuilt"] is True
    assert result["reason"] == "POLICY_MISMATCH"
    assert result["archived_paths"] == list(DERIVED_PATHS)
    assert ctx.read_json(MODE)["universe_policy_version"] == UNIVERSE_POLICY_VERSION
    for relative, raw in previous.items():
        assert ctx.read_bytes(f"{result['archive_directory']}/{relative}") == raw
        if relative != MODE:
            assert ctx.read_bytes(relative) is None
    for relative, raw in preserved.items():
        actual = ctx.read_bytes(relative)
        assert actual == raw
        assert hashlib.sha256(actual).digest() == hashlib.sha256(raw).digest()
    assert ctx.read_json(JOURNAL)["status"] == "COMPLETE"


def test_same_policy_is_idempotent_and_force_is_recoverable(ctx: QualificationContext) -> None:
    prior = seed(ctx, UNIVERSE_POLICY_VERSION)
    assert ensure_universe_policy(ctx)["rebuilt"] is False
    assert ensure_universe_policy(ctx)["archived_paths"] == []
    assert {relative: ctx.read_bytes(relative) for relative in DERIVED_PATHS} == prior

    forced = ensure_universe_policy(ctx, force=True)
    assert forced["reason"] == "FORCED"
    assert forced["archived_paths"] == list(DERIVED_PATHS)
    assert ensure_universe_policy(ctx)["rebuilt"] is False


def test_fresh_directory_has_no_migration(ctx: QualificationContext) -> None:
    result = ensure_universe_policy(ctx)
    assert result["rebuilt"] is False
    assert result["reason"] == "INITIALIZED"
    assert result["archive_directory"] is None
    assert ctx.read_json(MODE)["universe_policy_version"] == UNIVERSE_POLICY_VERSION
    assert ctx.read_bytes(JOURNAL) is None


@pytest.mark.parametrize("location", ["outputs/universe-provenance.json", "embedded"])
def test_preserved_replay_source_contains_no_classification_or_approvals(
    ctx: QualificationContext, location: str
) -> None:
    source = {
        "retrieved_at": "2026-09-17T10:00:00+00:00",
        "retrieval_environment": "live",
        "credential_binding_sha256": hashlib.sha256(b"synthetic binding").hexdigest(),
        "response_artifacts": {"instruments": ["synthetic hash", "artifacts/raw.json"]},
        "instrument_response_hash": hashlib.sha256(b"synthetic response").hexdigest(),
    }
    provenance = {
        **source,
        "universe_policy_version": "older-venue-policy",
        "stocks": [{"reason": "VENUE_COUNTRY_NOT_VERIFIED"}],
        "qualified": 1,
        "uk_venue_stocks": 0,
        "account_context": "STOCKS_AND_SHARES_ISA",
    }
    if location == "embedded":
        ctx.write_json("outputs/uk-isa-stock-universe.json", {"provenance": provenance})
    else:
        ctx.write_json(location, provenance)

    ensure_universe_policy(ctx)

    stored = ctx.read_json(REBUILD_SOURCE)
    assert stored["source"] == source
    assert stored["source_policy_version"] == "older-venue-policy"
    assert b"VENUE_COUNTRY_NOT_VERIFIED" not in (ctx.read_bytes(REBUILD_SOURCE) or b"")


def test_mixed_version_family_is_rebuilt_together(ctx: QualificationContext) -> None:
    seed(ctx, UNIVERSE_POLICY_VERSION)
    ctx.write_json("state/provider-stage.json", {"instruments": [], "version": "old"})
    assert ensure_universe_policy(ctx)["archived_paths"] == list(DERIVED_PATHS)


def test_malformed_projection_and_orphan_csv_are_invalidated(ctx: QualificationContext) -> None:
    ctx.write_bytes("outputs/providers-result.json", b"{broken json")
    first = ensure_universe_policy(ctx)
    assert first["reason"] == "POLICY_MISMATCH"
    ctx.write_bytes("outputs/uk-isa-stock-universe.csv", b"ticker\nSYNTHETIC\n")
    assert ensure_universe_policy(ctx)["reason"] == "POLICY_MISMATCH"


def test_interrupted_force_rebuild_resumes_even_with_current_policy(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from money.qualification import universe_policy

    previous = seed(ctx, UNIVERSE_POLICY_VERSION)
    rename = universe_policy.os.rename
    calls = 0

    def interrupt(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise OSError("synthetic interruption")
        rename(source, destination)

    monkeypatch.setattr(universe_policy.os, "rename", interrupt)
    with pytest.raises(OSError, match="synthetic interruption"):
        ensure_universe_policy(ctx, force=True)
    first_archive = ctx.read_json(JOURNAL)["archive_directory"]
    assert ctx.read_json(JOURNAL)["status"] == "IN_PROGRESS"
    monkeypatch.setattr(universe_policy.os, "rename", rename)

    result = ensure_universe_policy(ctx)

    assert result["reason"] == "INTERRUPTED_MIGRATION"
    assert ctx.read_json(JOURNAL)["status"] == "COMPLETE"
    for index, (relative, raw) in enumerate(previous.items()):
        archive = first_archive if index < 2 else result["archive_directory"]
        assert ctx.read_bytes(f"{archive}/{relative}") == raw


@pytest.mark.parametrize("unsafe", ["leaf", "parent", "archive", "source"])
def test_symlink_is_rejected_before_active_files_are_moved(
    ctx: QualificationContext, tmp_path: Path, unsafe: str
) -> None:
    ctx.write_json(MODE, {"version": "old-policy"})
    original = ctx.read_bytes(MODE)
    external = tmp_path / "external"
    external.mkdir()
    if unsafe == "leaf":
        target = ctx._path("outputs/providers-result.json")
        target.symlink_to(external / "absent.json")
    elif unsafe == "parent":
        (ctx.root / "outputs").symlink_to(external, target_is_directory=True)
    elif unsafe == "archive":
        (ctx.root / "state/universe-policy-history").symlink_to(external, target_is_directory=True)
    else:
        (ctx.root / REBUILD_SOURCE).symlink_to(external / "source.json")

    with pytest.raises(ValueError, match="QUALIFICATION_PATH_UNSAFE"):
        ensure_universe_policy(ctx)

    assert ctx.read_bytes(MODE) == original
    assert list(external.iterdir()) == []


def test_known_secret_in_projection_is_not_archived(ctx: QualificationContext) -> None:
    ctx.write_json(MODE, {"version": "old-policy", "bad": "fixture-only-sensitive-value"})
    original = ctx.read_bytes(MODE)
    ctx.environ = {"EODHD_API_KEY": "fixture-only-sensitive-value"}
    with pytest.raises(ValueError, match="QUALIFICATION_SECRET_DETECTED"):
        ensure_universe_policy(ctx)
    ctx.environ = {}
    assert ctx.read_bytes(MODE) == original
    assert ctx.read_bytes(JOURNAL) is None


def test_projection_larger_than_artifact_limit_is_archived_exactly(
    ctx: QualificationContext,
) -> None:
    # A universe projection is not a qualification artifact. Its separately
    # bounded migration must handle catalogues above the 2MB artifact ceiling.
    raw = b'{"large_synthetic_projection":"' + b"x" * 2_000_000 + b'"}\n'
    relative = "outputs/uk-isa-stock-universe.json"
    ctx._path(relative).write_bytes(raw)
    result = ensure_universe_policy(ctx)
    assert ctx.read_bytes(f"{result['archive_directory']}/{relative}", maximum=len(raw)) == raw


def test_oversized_projection_fails_before_any_move(
    ctx: QualificationContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    from money.qualification import universe_policy

    original = seed(ctx, None)
    monkeypatch.setattr(universe_policy, "MAXIMUM_PROJECTION_BYTES", 10)
    with pytest.raises(ValueError, match="QUALIFICATION_ARTIFACT_INVALID"):
        ensure_universe_policy(ctx)
    assert {relative: ctx.read_bytes(relative) for relative in DERIVED_PATHS} == original
    assert ctx.read_bytes(JOURNAL) is None


def test_malformed_migration_journal_requires_rebuild(ctx: QualificationContext) -> None:
    seed(ctx, UNIVERSE_POLICY_VERSION)
    ctx.write_bytes(JOURNAL, b"{interrupted journal")
    assert ensure_universe_policy(ctx)["reason"] == "INTERRUPTED_MIGRATION"
