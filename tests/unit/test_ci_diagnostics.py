"""CI diagnostics never publish assertion bodies or parameterized secrets."""

import json
import runpy
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = runpy.run_path(str(ROOT / "scripts/ci_diagnostics.py"))
SUMMARIZE = MODULE["summarize"]


def _junit_report(tmp_path, failure, captured="", name="test_worker"):
    suite = ElementTree.Element("testsuite")
    case = ElementTree.SubElement(suite, "testcase", name=name)
    ElementTree.SubElement(case, "failure").text = failure
    ElementTree.SubElement(case, "system-out").text = captured
    report = tmp_path / "results.xml"
    ElementTree.ElementTree(suite).write(report, encoding="utf-8")
    return report


def test_junit_summary_excludes_parameter_values_and_failure_body(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text(
        '<testsuites><testsuite><testcase name="test_api[PRIVATE]">'
        "<failure>SECRET assertion body</failure></testcase>"
        '<testcase name="test_pass"/></testsuite></testsuites>'
    )
    assert SUMMARIZE(report, "pytest") == "2 cases; 1 failed; test_api"


def test_advisory_summary_only_names_and_ids(tmp_path):
    report = tmp_path / "audit.json"
    report.write_text(
        json.dumps(
            {
                "dependencies": [
                    {
                        "name": "example",
                        "vulns": [
                            {"id": "GHSA-abcd-1234-abcd", "description": "PRIVATE"},
                        ],
                    }
                ]
            }
        )
    )
    assert SUMMARIZE(report, "audit") == (
        "1 advisory findings; 1 dependency records; example:GHSA-abcd-1234-abcd"
    )


@pytest.mark.parametrize(
    "kind,data",
    [
        ("pytest", '<!DOCTYPE xml [<!ENTITY foo "private">]><testsuites/>'),
        ("pytest", "not XML"),
        ("audit", "[]"),
        ("audit", "invalid JSON"),
    ],
)
def test_bad_reports_are_explicit_not_passed(tmp_path, kind, data):
    report = tmp_path / "bad"
    report.write_text(data)
    assert SUMMARIZE(report, kind) == "REPORT_INVALID"


def test_missing_report_is_not_success(tmp_path):
    assert SUMMARIZE(tmp_path / "absent", "pytest") == "REPORT_UNAVAILABLE"


def test_junit_summary_includes_only_bounded_relative_failure_location(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text("""<testsuites><testsuite><testcase name="test_worker">
<failure>assert token == "PRIVATE"
/private/customer/file.py:42: ValueError
tests/integration/test_postgres.py:56: AssertionError
src/money/worker.py:12: ValueError: PRIVATE
</failure></testcase></testsuite></testsuites>""")
    assert SUMMARIZE(report, "pytest") == (
        "1 cases; 1 failed; test_worker, "
        "tests/integration/test_postgres.py:56: AssertionError, "
        "src/money/worker.py:12: ValueError"
    )


def test_junit_summary_maps_checkout_frames_but_not_external_paths(tmp_path):
    report = _junit_report(
        tmp_path,
        (
            f'File "{ROOT}/src/money/worker.py", line 123, in run_once\n'
            'File "/private/customer/src/money/worker.py", line 456, in secret\n'
            f"{ROOT}/tests/integration/test_postgres.py:56: AssertionError: PRIVATE\n"
            "src/money/does_not_exist.py:12: ValueError\n"
            "src/money/../../tests/integration/test_postgres.py:12: ValueError\n"
        ),
    )
    assert SUMMARIZE(report, "pytest") == (
        "1 cases; 1 failed; test_worker, "
        "tests/integration/test_postgres.py:56: AssertionError, src/money/worker.py:123"
    )


def test_junit_summary_uses_only_known_codes_stages_and_relative_logger_location(tmp_path):
    report = _junit_report(
        tmp_path,
        "PRIVATE assertion",
        json.dumps(
            {
                "error_code": "FIRST_PASS_SNAPSHOT_MISMATCH",
                "stage": "FIRST_PASS_RESEARCH",
                "error_location": "money/worker.py:73",
                "api_key": "PRIVATE_API_KEY_VALUE",
                "database_url": "postgresql://private:password@internal-db",
            }
        ),
    )
    assert SUMMARIZE(report, "pytest") == (
        "1 cases; 1 failed; test_worker, src/money/worker.py:73, "
        "code=FIRST_PASS_SNAPSHOT_MISMATCH, stage=FIRST_PASS_RESEARCH"
    )


def test_passing_case_logs_are_not_reported_as_failure_evidence(tmp_path):
    report = tmp_path / "results.xml"
    report.write_text("""<testsuite><testcase name="test_pass"><system-out>
{"code":"RESEARCH_FAILED", "stage":"FAILED", "error_location":"money/worker.py:73"}
</system-out></testcase></testsuite>""")
    assert SUMMARIZE(report, "pytest") == "1 cases; 0 failed"


@pytest.mark.parametrize("line", ["0", "-1", "1234567", "12PRIVATE"])
def test_invalid_source_line_is_not_exposed(tmp_path, line):
    report = _junit_report(tmp_path, f"src/money/worker.py:{line}: ValueError: PRIVATE")
    assert SUMMARIZE(report, "pytest") == "1 cases; 1 failed; test_worker"


def test_exception_class_is_a_finite_vocabulary(tmp_path):
    report = _junit_report(tmp_path, "src/money/worker.py:12: PRIVATE_API_KEY: private")
    assert SUMMARIZE(report, "pytest") == "1 cases; 1 failed; test_worker, src/money/worker.py:12"


@pytest.mark.parametrize(
    "payload",
    [
        b"<other/>",
        b"<testsuite>\x00</testsuite>",
        '<!DOCTYPE xml [<!ENTITY foo "private">]><testsuites/>'.encode("utf-16"),
        b"\xff<testsuite/>",
    ],
)
def test_untrusted_junit_encoding_or_shape_is_rejected(tmp_path, payload):
    report = tmp_path / "results.xml"
    report.write_bytes(payload)
    assert SUMMARIZE(report, "pytest") == "REPORT_INVALID"


def test_oversized_and_symlinked_reports_are_unavailable(tmp_path):
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b" " * (MODULE["MAX_BYTES"] + 1))
    assert SUMMARIZE(oversized, "pytest") == "REPORT_UNAVAILABLE"
    report = tmp_path / "results.xml"
    report.write_text("<testsuite/>")
    symlink = tmp_path / "symlink.xml"
    symlink.symlink_to(report)
    assert SUMMARIZE(symlink, "pytest") == "REPORT_UNAVAILABLE"


def test_summary_is_bounded_without_losing_first_failure_location(tmp_path):
    report = _junit_report(
        tmp_path,
        "\n".join(
            f"tests/integration/test_postgres.py:{line}: AssertionError: PRIVATE"
            for line in range(1, 30)
        ),
    )
    summary = SUMMARIZE(report, "pytest")
    assert len(summary) <= 500
    assert summary.startswith("1 cases; 1 failed; test_worker, ")
    assert "tests/integration/test_postgres.py:1: AssertionError" in summary
    assert "PRIVATE" not in summary


def test_smoke_summary_extracts_stage_and_checkout_node_frame_only(tmp_path):
    report = tmp_path / "smoke.log"
    report.write_text(
        "Smoke failed during persisted packet retrieval: PRIVATE response\n"
        f"    at run (file://{ROOT}/apps/web/scripts/smoke.mjs:123:8)\n"
        "    at secret (file:///private/customer/smoke.mjs:456:7)\n"
        "postgresql://username:password@private-db\n"
    )
    assert SUMMARIZE(report, "smoke") == (
        "FAILURE_DIAGNOSTICS; stage=PACKET_RETRIEVAL, apps/web/scripts/smoke.mjs:123"
    )


def test_unknown_smoke_content_is_not_success_or_exposed(tmp_path):
    report = tmp_path / "smoke.log"
    report.write_text("Smoke failed during PRIVATE_ACCOUNT: PRIVATE_API_KEY\nSuccess!\n")
    assert SUMMARIZE(report, "smoke") == "NO_SAFE_FAILURE_DIAGNOSTICS"


def test_github_output_contains_one_safe_line(tmp_path):
    report = _junit_report(
        tmp_path,
        ("::warning::PRIVATE\nsummary=PRIVATE\nsrc/money/worker.py:12: ValueError: PRIVATE\n"),
        name="test_worker[PRIVATE]",
    )
    output = tmp_path / "github-output"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/ci_diagnostics.py"),
            "pytest",
            str(report),
            "--github-output",
            str(output),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    expected = "1 cases; 1 failed; test_worker, src/money/worker.py:12: ValueError\n"
    assert completed.stdout == expected
    assert completed.stderr == ""
    assert output.read_text() == "summary=" + expected


def test_backend_summary_exposes_phase_not_private_paths_or_exception_values(tmp_path):
    report = tmp_path / "backend.json"
    report.write_text(
        json.dumps(
            {
                "status": "FAILED",
                "phase": "migrations",
                "code": "COMMAND_FAILED",
                "failure_class": "secret\nvalue",
                "retained_directory": "/private/customer",
                "error": "postgresql://password@private-host",
            }
        )
    )
    assert SUMMARIZE(report, "acceptance") == "FAILED; phase=migrations; code=COMMAND_FAILED"


@pytest.mark.parametrize("payload", [{"status": "should work"}, [], "not json"])
def test_backend_summary_never_infers_acceptance(tmp_path, payload):
    report = tmp_path / "backend.json"
    report.write_text(json.dumps(payload))
    assert SUMMARIZE(report, "acceptance") == "REPORT_INVALID"


def test_backend_summary_rejects_secret_shaped_like_identifier(tmp_path):
    report = tmp_path / "backend.json"
    report.write_text(
        json.dumps(
            {
                "status": "FAILED",
                "phase": "private_customer",
                "code": "PRIVATE_API_KEY",
                "failure_class": "PrivateAccountException",
            }
        )
    )
    assert SUMMARIZE(report, "acceptance") == "FAILED"
