"""Safety contracts for the runner; these mocks do not qualify PostgreSQL behavior."""

import runpy
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/backend_acceptance.py"))


def test_acceptance_subprocess_environment_drops_external_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "not-an-acceptance-database")
    monkeypatch.setenv("TEST_DATABASE_URL", "not-an-acceptance-database")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-key")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "test-only-not-a-token")
    result = MODULE["isolated_environment"]()
    assert not set(result) & {
        "DATABASE_URL",
        "TEST_DATABASE_URL",
        "OPENAI_API_KEY",
        "AWS_SESSION_TOKEN",
    }
    assert set(result) <= set(MODULE["ENVIRONMENT_KEYS"])


def test_failed_commands_never_expose_diagnostics_or_secrets():
    with pytest.raises(MODULE["AcceptanceFailure"]) as caught:
        MODULE["command"](
            [sys.executable, "-c", "print('private-provider-response'); raise SystemExit(1)"],
            MODULE["isolated_environment"](),
        )
    assert str(caught.value) == "COMMAND_FAILED"
    assert "private-provider" not in str(caught.value)


def test_environment_denial_is_blocked_not_a_pass():
    with pytest.raises(MODULE["AcceptanceFailure"]) as caught:
        MODULE["command"](
            [sys.executable, "-c", "print('Operation not permitted'); raise SystemExit(1)"],
            MODULE["isolated_environment"](),
        )
    assert caught.value.blocked
    assert caught.value.code == "ENVIRONMENT_PERMISSION_DENIED"


def test_command_timeout_is_bounded():
    with pytest.raises(MODULE["AcceptanceFailure"], match="COMMAND_TIMEOUT"):
        MODULE["command"](
            [sys.executable, "-c", "import time; time.sleep(30)"],
            MODULE["isolated_environment"](),
            timeout=1,
        )


def test_missing_postgres_tools_are_explicitly_blocked(monkeypatch):
    monkeypatch.setattr(MODULE["shutil"], "which", lambda name: None)
    result = MODULE["run_acceptance"]()
    assert result["status"] == "BLOCKED_ENVIRONMENT"
    assert result["steps"] == []
    assert result["production_qualified"] is False


@pytest.mark.parametrize("stop_succeeds", (True, False))
def test_only_owned_cluster_is_targeted_and_cleanup_requires_confirmed_shutdown(
    monkeypatch, tmp_path, stop_succeeds
):
    run = MODULE["run_acceptance"]
    namespace = run.__globals__
    commands = []
    real_temporary = MODULE["tempfile"].TemporaryDirectory

    def own_directory(**kwargs):
        return real_temporary(prefix="money-acceptance-", dir=tmp_path, delete=False)

    def fake_command(argv, environment, **kwargs):
        commands.append((argv, dict(environment)))
        return ""

    monkeypatch.setattr(MODULE["shutil"], "which", lambda name: f"/fixture/{name}")
    monkeypatch.setattr(MODULE["tempfile"], "TemporaryDirectory", own_directory)
    monkeypatch.setitem(namespace, "command", fake_command)
    monkeypatch.setitem(
        namespace, "exercise_recovery", lambda *args: {"packet_hash": "fixture-only"}
    )
    monkeypatch.setitem(namespace, "verify_restored", lambda *args: None)
    monkeypatch.setitem(namespace, "stop_owned_cluster", lambda *args: stop_succeeds)
    result = run(run_tests=False)
    assert result["production_qualified"] is False
    assert result["temporary_data_removed"] is stop_succeeds
    assert result["status"] == ("VERIFIED" if stop_succeeds else "FAILED")
    assert any(step["status"] == "NOT_RUN" for step in result["steps"])
    for argv, environment in commands:
        assert "shell" not in argv
        if "DATABASE_URL" in environment:
            assert "money_acceptance" in environment["DATABASE_URL"]
            assert str(tmp_path) in unquote(environment["DATABASE_URL"])
            assert environment["MONEY_RESEARCH_MODE"] == "demo"
            assert environment["MONEY_ENV"] == "test"
        assert not set(environment) & {"OPENAI_API_KEY", "AWS_SESSION_TOKEN"}
    restore = next(argv for argv, _ in commands if argv[0] == "pg_restore")
    migrations = [argv for argv, _ in commands if argv[1:] == ["-m", "alembic", "upgrade", "head"]]
    assert len(migrations) == 3
    assert "--dbname=money_restore" in restore and "--exit-on-error" in restore
    assert "--clean" not in restore and "--create" not in restore
    if stop_succeeds:
        assert list(tmp_path.iterdir()) == []
    else:
        assert Path(result["retained_directory"]).parent == tmp_path
        assert Path(result["retained_directory"]).is_dir()


def test_runner_does_not_accept_a_target_database(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["backend_acceptance.py", "--database-url", "fixture"])
    with pytest.raises(SystemExit) as result:
        MODULE["main"]()
    assert result.value.code == 2
    assert "fixture" not in capsys.readouterr().err
