"""Aggregate-only acceptance telemetry; never serialize test diagnostics."""

from __future__ import annotations

import json
from typing import Any

_counts = {
    name: 0
    for name in ("passed", "failed", "errors", "skipped", "xfailed", "xpassed", "deselected")
}


def pytest_sessionstart(session: Any) -> None:
    for name in _counts:
        _counts[name] = 0


def pytest_deselected(items: Any) -> None:
    _counts["deselected"] += len(items)


def pytest_runtest_logreport(report: Any) -> None:
    if hasattr(report, "wasxfail"):
        _counts["xfailed" if report.skipped else "xpassed"] += 1
    elif report.skipped:
        _counts["skipped"] += 1
    elif report.failed:
        _counts["failed" if report.when == "call" else "errors"] += 1
    elif report.when == "call" and report.passed:
        _counts["passed"] += 1


def pytest_sessionfinish(session: Any, exitstatus: Any) -> None:
    print(
        "\nMONEY_QUALIFICATION_TEST_COUNTS="
        + json.dumps({"collected": session.testscollected, **_counts}, sort_keys=True)
    )
