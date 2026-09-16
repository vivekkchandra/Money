"""CI diagnostics never publish assertion bodies or parameterized secrets."""

import json
import runpy
from pathlib import Path

import pytest

SUMMARIZE = runpy.run_path(
    str(Path(__file__).resolve().parents[2] / "scripts/ci_diagnostics.py")
)["summarize"]


def test_junit_summary_excludes_parameter_values_and_failure_body(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text('<testsuites><testsuite><testcase name="test_api[PRIVATE]">'
                      '<failure>SECRET assertion body</failure></testcase>'
                      '<testcase name="test_pass"/></testsuite></testsuites>')
    assert SUMMARIZE(report, "pytest") == "2 cases; 1 failed; test_api"


def test_advisory_summary_only_names_and_ids(tmp_path):
    report = tmp_path / "audit.json"
    report.write_text(json.dumps({"dependencies": [{"name": "example", "vulns": [
        {"id": "GHSA-abcd-1234-abcd", "description": "PRIVATE"},
    ]}]}))
    assert SUMMARIZE(report, "audit") == (
        "1 advisory findings; 1 dependency records; example:GHSA-abcd-1234-abcd"
    )


@pytest.mark.parametrize("kind,data", [
    ("pytest", '<!DOCTYPE xml [<!ENTITY foo "private">]><testsuites/>'),
    ("pytest", "not XML"), ("audit", "[]"), ("audit", "invalid JSON"),
])
def test_bad_reports_are_explicit_not_passed(tmp_path, kind, data):
    report = tmp_path / "bad"
    report.write_text(data)
    assert SUMMARIZE(report, kind) == "REPORT_INVALID"


def test_missing_report_is_not_success(tmp_path):
    assert SUMMARIZE(tmp_path / "absent", "pytest") == "REPORT_UNAVAILABLE"
