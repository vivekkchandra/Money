"""Synthetic scheduler state is an optimization, never real qualification evidence."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from money.qualification.core import QualificationContext
from money.qualification.universe_policy import UNIVERSE_POLICY_VERSION
from money.qualification.universe_progress import (
    CURSOR,
    enrichment_order,
    record_network_progress,
)


@pytest.fixture
def ctx(tmp_path: Path) -> QualificationContext:
    return QualificationContext(tmp_path / "bundle", tmp_path, {}, datetime.now(UTC))


def rows() -> list[dict[str, Any]]:
    return [
        {"trading212_id": name, "isin": name, "provider_enrichment_input": True}
        for name in ("A", "B", "C")
    ]


def test_rotation_and_membership_churn(ctx: QualificationContext) -> None:
    source = rows()
    record_network_progress(ctx, source[1], None)
    assert enrichment_order(ctx, source, None, replay=False) == [2, 0, 1]
    del source[1]
    assert enrichment_order(ctx, source, None, replay=False) == [1, 0]


@pytest.mark.parametrize("mismatch", ["version", "universe_policy_version", "credential_binding_sha256"])
def test_mismatched_cursor_ignored(ctx: QualificationContext, mismatch: str) -> None:
    source = rows()
    record_network_progress(ctx, source[0], None)
    state = ctx.read_json(CURSOR)
    assert state["universe_policy_version"] == UNIVERSE_POLICY_VERSION
    state[mismatch] = "different"
    ctx.write_json(CURSOR, state)
    assert enrichment_order(ctx, source, None, replay=False) == [0, 1, 2]


def test_replay_preserves_live_cursor_and_never_excludes_candidates(ctx: QualificationContext) -> None:
    source = rows()
    record_network_progress(ctx, source[0], None)
    before = ctx.read_bytes(CURSOR)
    assert enrichment_order(ctx, source, None, replay=True) == [0, 1, 2]
    assert ctx.read_bytes(CURSOR) == before
    source[1]["provider_enrichment_input"] = False
    assert enrichment_order(ctx, source, None, replay=False) == [2, 0]


def test_corrupt_cursor_does_not_omit_stocks(ctx: QualificationContext) -> None:
    ctx.write_bytes(CURSOR, b"{")
    assert enrichment_order(ctx, rows(), None, replay=False) == [0, 1, 2]
