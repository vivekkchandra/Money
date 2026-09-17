"""Bulk operator projections are large; qualification proof limits stay bounded."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from money.qualification.core import MAXIMUM_BYTES, QualificationContext
from money.qualification.runner import _execute


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    return QualificationContext(tmp_path / "bundle", tmp_path, {
        "TRADING212_API_KEY": "synthetic-bulk-projection-secret-not-real",
    }, datetime(2026, 9, 17, tzinfo=UTC))


def test_large_bulk_provider_projection_does_not_relax_artifact_limit(ctx: QualificationContext) -> None:
    ctx.write_json("state/bulk-universe-mode.json", {"version": "money-bulk-universe-v1"})
    result = {"complete": False, "synthetic_projection": "x" * (MAXIMUM_BYTES + 1)}
    assert _execute(ctx, "providers", lambda: result) == result
    path = ctx.root / "outputs/providers-result.json"
    raw = path.read_bytes()
    assert len(raw) > MAXIMUM_BYTES
    assert json.loads(raw) == result
    assert ctx.blockers == []
    with pytest.raises(ValueError, match="QUALIFICATION_FILE_SIZE_INVALID"):
        ctx.artifact(raw)
    assert not list((ctx.root / "artifacts").glob("*"))


@pytest.mark.parametrize("name,bulk_mode", [("providers", False), ("qlib", True), ("snapshot", True)])
def test_large_projection_exception_does_not_extend_to_other_steps(ctx: QualificationContext, name: str, bulk_mode: bool) -> None:
    if bulk_mode:
        ctx.write_json("state/bulk-universe-mode.json", {"version": "money-bulk-universe-v1"})
    result = {"synthetic_projection": "x" * (MAXIMUM_BYTES + 1)}
    assert _execute(ctx, name, lambda: result) == {}
    assert not (ctx.root / f"outputs/{name}-result.json").exists()
    assert ctx.blockers[0]["code"] == name.upper() + "_FAILED"


def test_large_projection_still_rejects_secret_echo(ctx: QualificationContext, capsys: pytest.CaptureFixture[str]) -> None:
    ctx.write_json("state/bulk-universe-mode.json", {"version": "money-bulk-universe-v1"})
    secret = ctx.environ["TRADING212_API_KEY"]
    result = {"synthetic_projection": "x" * (MAXIMUM_BYTES + 1), "unsafe_echo": secret}
    assert _execute(ctx, "providers", lambda: result) == {}
    assert not (ctx.root / "outputs/providers-result.json").exists()
    captured = capsys.readouterr()
    assert secret not in captured.out + captured.err
    for path in ctx.root.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()
