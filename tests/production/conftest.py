"""Genuine manifest selection for opt-in tests; no synthetic production fallback."""

import runpy
from pathlib import Path

import pytest

from money.schemas.contracts import utc_now

_ACCEPTANCE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_live_isa.py"))


def _outcome(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except _ACCEPTANCE["LiveAcceptanceFailure"] as error:
        if error.status.startswith("SKIPPED_") or error.status == "BLOCKED_EXTERNAL_INFRA":
            pytest.skip(f"{error.status}: {error.code}")
        pytest.fail(f"{error.status}: {error.code}", pytrace=False)


@pytest.fixture
def live_manifest():
    return _outcome(_ACCEPTANCE["configured_manifest"])


@pytest.fixture
def live_selector(live_manifest):
    def select(**kwargs):
        return _outcome(_ACCEPTANCE["select_candidates"], live_manifest, utc_now(), **kwargs)[0]

    return select


@pytest.fixture
def live_instrument(live_selector):
    return live_selector()
