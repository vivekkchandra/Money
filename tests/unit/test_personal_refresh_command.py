"""The operator wrapper is bounded, quote-safe and stops on failed prerequisites."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "refresh_personal.sh"
ISIN = "GB0006389398"
POLICY_ENVIRONMENT = {
    "MONEY_USAGE_MODE": "personal_research",
    "MONEY_QLIB_ENABLED": "false",
    "MONEY_ISSUER_SOURCE_POLICY": "official_disclosures",
    "MONEY_INFERENCE_CONFIG": "data/configuration/ollama-inference.json",
}


@pytest.fixture
def command_harness(tmp_path):
    """Replace uv only; no qualification code, provider request or artifact runs."""
    binary = tmp_path / "bin"
    binary.mkdir()
    log = tmp_path / "commands.jsonl"
    fake = tmp_path / "fake_uv.py"
    fake.write_text(
        "import json, os, sys\n"
        f"names = {tuple(POLICY_ENVIRONMENT)!r}\n"
        "record = {'args': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "          'policy': {name: os.environ.get(name) for name in names}}\n"
        "with open(os.environ['MONEY_TEST_COMMAND_LOG'], 'a') as output:\n"
        "    output.write(json.dumps(record) + '\\n')\n"
        "if sys.argv[3] == os.environ.get('MONEY_TEST_FAIL_SCRIPT'):\n"
        "    raise SystemExit(int(os.environ['MONEY_TEST_FAIL_STATUS']))\n",
        encoding="utf-8",
    )
    executable = binary / "uv"
    executable.write_text(
        f"#!/bin/sh\nexec {shlex.quote(sys.executable)} {shlex.quote(str(fake))} \"$@\"\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    environment = {
        "PATH": f"{binary}:/usr/bin:/bin",
        "MONEY_TEST_COMMAND_LOG": str(log),
        # Synthetic sentinel: do not copy the real process environment/secrets.
        "EODHD_API_KEY": "synthetic-secret-must-never-print",
        "MONEY_QLIB_ENABLED": "true",
        "MONEY_USAGE_MODE": "hosted_commercial_production",
    }
    return tmp_path, log, environment


def invoke(command_harness, arguments, *, fail_script=None, status=2):
    workdir, log, environment = command_harness
    if fail_script is not None:
        environment = {
            **environment,
            "MONEY_TEST_FAIL_SCRIPT": fail_script,
            "MONEY_TEST_FAIL_STATUS": str(status),
        }
    result = subprocess.run(
        ["/bin/sh", str(SCRIPT), *arguments],
        cwd=workdir, env=environment, capture_output=True, text=True, timeout=10, check=False,
    )
    records = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    assert environment["EODHD_API_KEY"] not in result.stdout + result.stderr + json.dumps(records)
    return result, records


def test_personal_refresh_has_valid_posix_shell_syntax():
    result = subprocess.run(
        ["/bin/sh", "-n", str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, timeout=5, check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("arguments", [
    [], [ISIN, ISIN], [""], ["GB000638939"], ["GB00063893980"],
    ["gb0006389398"], ["../issuer-id"], ["GB000638;398"], ["GB000638\n398"],
])
def test_invalid_arguments_stop_before_uv(command_harness, arguments):
    result, records = invoke(command_harness, arguments)
    assert result.returncode == 2
    assert records == []
    assert "Usage:" in result.stderr or "Invalid ISIN" in result.stderr


def test_personal_refresh_does_not_gate_on_company_capture_and_preserves_selection(command_harness):
    result, records = invoke(command_harness, [ISIN])
    assert result.returncode == 0
    assert [record["args"] for record in records] == [
        ["run", "python", "scripts/run_research_testing.py", "--isin", ISIN],
    ]
    assert all(record["policy"] == POLICY_ENVIRONMENT for record in records)
    assert all(record["cwd"] == str(REPO) for record in records)
    assert "env" not in result.stdout


@pytest.mark.parametrize("failed_script,status,expected_scripts", [
    ("scripts/run_research_testing.py", 2, ["scripts/run_research_testing.py"]),
])
def test_failed_prerequisite_stops_without_later_stage(
    command_harness, failed_script, status, expected_scripts,
):
    result, records = invoke(command_harness, [ISIN], fail_script=failed_script, status=status)
    assert result.returncode == status
    assert [record["args"][2] for record in records] == expected_scripts
    assert all(record["policy"] == POLICY_ENVIRONMENT for record in records)


def test_wrapper_does_not_mutate_parent_configuration(command_harness, monkeypatch):
    monkeypatch.setenv("MONEY_QLIB_ENABLED", "true")
    result, _ = invoke(command_harness, [ISIN])
    assert result.returncode == 0
    assert os.environ["MONEY_QLIB_ENABLED"] == "true"
