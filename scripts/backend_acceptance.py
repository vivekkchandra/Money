"""Isolated real-PostgreSQL durability/restore drill; never accepts an existing DB.

The only research is explicitly synthetic DEMO.L. This is operational acceptance,
not production data/native-firm qualification. Exit 0 verified, 1 failed, 2 blocked.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import timedelta
from pathlib import Path
from typing import Any, Never

from sqlalchemy import select
from sqlalchemy.engine import URL

from money.schemas.contracts import ResearchMandate, utc_now
from money.storage import Claim, LeaseLost, ResearchStore
from money.storage import models as db
from money.storage.store import aware, digest

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ("initdb", "pg_ctl", "createdb", "pg_dump", "pg_restore")
ENVIRONMENT_KEYS = ("PATH", "LANG", "LC_ALL", "TZ", "SYSTEMROOT", "TMPDIR")


class AcceptanceFailure(Exception):
    def __init__(self, code: str, *, blocked: bool = False) -> None:
        self.code, self.blocked = code, blocked
        super().__init__(code)


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        # Unknown options could themselves contain an accidentally supplied URL/key.
        self.exit(2, "Invalid acceptance arguments; use --help.\n")


def isolated_environment() -> dict[str, str]:
    """No inherited database, provider, broker, cloud or LLM credentials."""
    return {key: os.environ[key] for key in ENVIRONMENT_KEYS if key in os.environ}


def command(argv: list[str], environment: dict[str, str], *, timeout: int = 60) -> str:
    """Bound output and runtime; never print raw diagnostics or command credentials."""
    try:
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                argv,
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
            )
            try:
                returncode = process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                else:
                    process.kill()
                process.wait(timeout=10)
                raise
            output.seek(0)
            text = output.read(128_000).decode("utf-8", errors="replace")
    except FileNotFoundError as error:
        raise AcceptanceFailure("EXECUTABLE_UNAVAILABLE", blocked=True) from error
    except subprocess.TimeoutExpired as error:
        raise AcceptanceFailure("COMMAND_TIMEOUT") from error
    if returncode:
        denied = any(
            value in text.lower() for value in ("operation not permitted", "permission denied")
        )
        raise AcceptanceFailure(
            "ENVIRONMENT_PERMISSION_DENIED" if denied else "COMMAND_FAILED", blocked=denied
        )
    return text


def checkpoint_worker(database_url: str, channel: Any) -> None:
    """Fixed synthetic crash target: commit one report, then await an abrupt kill."""
    safe_environment = isolated_environment()
    os.environ.clear()
    os.environ.update(safe_environment)
    from money.flows.research import build_runtime

    store = ResearchStore(database_url)
    try:
        claim = store.claim_job("acceptance-crash", lease_seconds=10, job_timeout_seconds=120)
        if claim is None:
            raise RuntimeError("no synthetic job")
        owned, runtime = store.for_claim(claim), build_runtime("demo")
        instrument = runtime.eligibility.get_instrument_metadata("DEMO.L")
        assert instrument is not None
        owned.save_artifact(claim.job_id, "eligibility", instrument)
        owned.update_stage(claim.job_id, "SNAPSHOT_BUILD")
        snapshot = runtime.snapshot_builder(instrument)
        owned.save_snapshot(claim.job_id, snapshot)
        owned.update_stage(claim.job_id, "FIRST_PASS_RESEARCH")
        firm = next(
            firm for firm in runtime.firms if getattr(firm, "firm", None) == "tradingagents"
        )
        report = firm.research(ResearchMandate(), snapshot)
        owned.save_report(claim.job_id, report.firm, report)
        channel.send(
            {"claim": asdict(claim), "sealed_report_hash": digest(report.model_dump(mode="json"))}
        )
        while True:
            time.sleep(1)
    finally:
        store.engine.dispose()
        channel.close()


def exercise_recovery(database_url: str, environment: dict[str, str]) -> dict[str, Any]:
    """Actual killed process, elapsed lease recovery and independently started worker."""
    from fastapi.testclient import TestClient

    from money.api.app import create_app
    from money.api.settings import Settings

    store = ResearchStore(database_url)
    token = environment["RESEARCH_API_TOKEN"]
    settings = Settings.model_validate(
        {
            "database_url": database_url,
            "money_env": "test",
            "money_research_mode": "demo",
            "research_api_token": token,
        }
    )
    try:
        with TestClient(create_app(settings, store)) as client:
            assert client.get("/research/jobs").status_code == 401
            created = client.post(
                "/research/jobs",
                json={"ticker": "DEMO.L"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Idempotency-Key": "acceptance-recovery",
                },
            )
            assert created.status_code == 202 and created.json()["status"] == "QUEUED"
            job_id = created.json()["id"]
        context = multiprocessing.get_context("spawn")
        parent, child = context.Pipe(duplex=False)
        process = context.Process(target=checkpoint_worker, args=(database_url, child))
        process.start()
        child.close()
        try:
            if not parent.poll(30):
                raise AcceptanceFailure("CHECKPOINT_TIMEOUT")
            checkpoint = parent.recv()
        finally:
            process.kill()
            process.join(timeout=10)
            process.close()
            parent.close()
        old_claim = Claim(**checkpoint["claim"])
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = store.get_job(job_id)  # This invokes durable expired-lease recovery.
            with store.engine.connect() as connection:
                available = connection.scalar(
                    select(db.jobs.c.available_at).where(db.jobs.c.id == job_id)
                )
            if (
                result
                and result["status"] == "QUEUED"
                and available
                and aware(available) <= utc_now()
            ):
                break
            time.sleep(0.1)
        else:
            raise AcceptanceFailure("LEASE_RECOVERY_TIMEOUT")
        try:
            store.for_claim(old_claim).renew_lease(job_id)
        except LeaseLost:
            pass
        else:
            raise AcceptanceFailure("STALE_WORKER_NOT_FENCED")
        command([sys.executable, "-m", "money.worker", "--once"], environment, timeout=60)
        result = store.get_job(job_id)
        assert result and result["status"] == "COMPLETE" and result["attempt_count"] == 2
        assert result["packet"]["runtime"] == "demo" and result["packet"]["signal"] is None
        with store.engine.connect() as connection:
            reports = dict(
                connection.execute(
                    select(db.firm_reports.c.firm, db.firm_reports.c.content_hash).where(
                        db.firm_reports.c.job_id == job_id
                    )
                ).tuples()
            )
        assert len(reports) == 3 and reports["tradingagents"] == checkpoint["sealed_report_hash"]
        session_hash, version = hashlib.sha256(token.encode()).hexdigest(), "a" * 64
        store.create_session(session_hash, version, utc_now() + timedelta(hours=1))
        return {
            "job_id": job_id,
            "packet_hash": digest(result["packet"]),
            "report_hashes": reports,
            "session_hash": session_hash,
            "credential_version": version,
        }
    finally:
        store.engine.dispose()


def verify_restored(database_url: str, expected: dict[str, Any]) -> None:
    from sqlalchemy.exc import DatabaseError

    store = ResearchStore(database_url)
    try:
        job = store.get_job(expected["job_id"])
        assert (
            job and job["status"] == "COMPLETE" and digest(job["packet"]) == expected["packet_hash"]
        )
        assert store.session_valid(expected["session_hash"], expected["credential_version"])
        with store.engine.connect() as connection:
            reports = dict(
                connection.execute(
                    select(db.firm_reports.c.firm, db.firm_reports.c.content_hash).where(
                        db.firm_reports.c.job_id == expected["job_id"]
                    )
                ).tuples()
            )
        assert reports == expected["report_hashes"]
        try:
            with store.engine.begin() as connection:
                connection.execute(
                    db.packets.update()
                    .where(db.packets.c.job_id == expected["job_id"])
                    .values(content_hash="0" * 64)
                )
        except DatabaseError:
            pass
        else:
            raise AcceptanceFailure("RESTORED_IMMUTABILITY_MISSING")
    finally:
        store.engine.dispose()


def stop_owned_cluster(data: Path, environment: dict[str, str]) -> bool:
    """Do not delete a cluster unless shutdown (or absence) is confirmed."""
    try:
        command(["pg_ctl", "-D", str(data), "-w", "-t", "30", "stop", "-m", "fast"], environment)
        return True
    except AcceptanceFailure:
        try:
            status = subprocess.run(
                ["pg_ctl", "-D", str(data), "status"],
                cwd=ROOT,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            return status.returncode == 3  # documented: server not running
        except (OSError, subprocess.TimeoutExpired):
            return False


def exercise_migrations(environment: dict[str, str]) -> None:
    """Three real release processes must serialize on the same fresh PostgreSQL DB."""
    with ThreadPoolExecutor(max_workers=3) as executor:
        attempts = [
            executor.submit(
                command, [sys.executable, "-m", "alembic", "upgrade", "head"], environment
            )
            for _ in range(3)
        ]
        for attempt in attempts:
            attempt.result()
    command([sys.executable, "-m", "alembic", "check"], environment)


def run_acceptance(*, run_tests: bool = True) -> dict[str, Any]:
    report: dict[str, Any] = {
        "scope": "ISOLATED_LOCAL_POSTGRESQL_SYNTHETIC_RESEARCH",
        "production_qualified": False,
        "status": "FAILED",
        "steps": [],
    }
    missing = [name for name in TOOLS if shutil.which(name) is None]
    if missing:
        return {
            **report,
            "status": "BLOCKED_ENVIRONMENT",
            "code": "POSTGRESQL_TOOLS_MISSING",
            "missing_tools": missing,
        }
    environment = isolated_environment()
    temporary_parent = "/private/tmp" if Path("/private/tmp").is_dir() else None
    # No existing database URL or directory may be supplied to this runner.
    temporary_directory = tempfile.TemporaryDirectory(
        prefix="money-acceptance-", dir=temporary_parent, delete=False
    )
    stopped = True
    with temporary_directory as temporary:
        base = Path(temporary)
        data, sockets, backup, log = (
            base / "data",
            base / "sockets",
            base / "backup.dump",
            base / "postgres.log",
        )
        sockets.mkdir(mode=0o700)
        start_attempted = False
        phase = "postgres_init"
        try:
            command(
                [
                    "initdb",
                    "-D",
                    str(data),
                    "--username=money_acceptance",
                    "--auth-local=trust",
                    "--auth-host=reject",
                    "--encoding=UTF8",
                    "--no-locale",
                    "--data-checksums",
                ],
                environment,
            )
            phase = "postgres_start"
            start_attempted, stopped = True, False
            command(
                [
                    "pg_ctl",
                    "-D",
                    str(data),
                    "-l",
                    str(log),
                    "-w",
                    "-t",
                    "30",
                    "-o",
                    f"-h '' -k {sockets} -c unix_socket_permissions=0700",
                    "start",
                ],
                environment,
            )
            report["steps"].append({"requirement": "isolated_postgresql", "status": "VERIFIED"})
            for name in ("money_acceptance", "money_restore"):
                command(
                    ["createdb", "-h", str(sockets), "-U", "money_acceptance", name], environment
                )
            url = URL.create(
                "postgresql+psycopg",
                username="money_acceptance",
                database="money_acceptance",
                query={"host": str(sockets)},
            ).render_as_string(hide_password=False)
            environment.update(
                DATABASE_URL=url,
                TEST_DATABASE_URL=url,
                MONEY_ENV="test",
                MONEY_RESEARCH_MODE="demo",
                RESEARCH_API_TOKEN=secrets.token_urlsafe(32),
                MONEY_JOB_TIMEOUT_SECONDS="30",
            )
            phase = "migrations"
            exercise_migrations(environment)
            report["steps"].append(
                {"requirement": phase, "status": "VERIFIED", "concurrent_release_processes": 3}
            )
            if run_tests:
                phase = "postgresql_integration_tests"
                command(
                    [
                        sys.executable,
                        "-m",
                        "pytest",
                        "tests/integration/test_postgres.py",
                        "tests/integration/test_budget_recovery.py",
                        "-q",
                        "--tb=short",
                    ],
                    environment,
                    timeout=240,
                )
                report["steps"].append({"requirement": phase, "status": "VERIFIED"})
            else:
                report["steps"].append(
                    {"requirement": "postgresql_integration_tests", "status": "NOT_RUN"}
                )
            phase = "worker_crash_recovery"
            expected = exercise_recovery(url, environment)
            report["steps"].append(
                {"requirement": phase, "status": "VERIFIED", "sealed_reports": 3}
            )
            phase = "backup"
            command(
                [
                    "pg_dump",
                    "-h",
                    str(sockets),
                    "-U",
                    "money_acceptance",
                    "--format=custom",
                    "--file",
                    str(backup),
                    "money_acceptance",
                ],
                environment,
            )
            phase = "postgres_restart"
            command(
                ["pg_ctl", "-D", str(data), "-w", "-t", "30", "restart", "-m", "fast"], environment
            )
            verify_restored(url, expected)
            report["steps"].append({"requirement": phase, "status": "VERIFIED"})
            phase = "restore_empty_database"
            command(
                [
                    "pg_restore",
                    "-h",
                    str(sockets),
                    "-U",
                    "money_acceptance",
                    "--no-owner",
                    "--no-privileges",
                    "--exit-on-error",
                    "--single-transaction",
                    "--dbname=money_restore",
                    str(backup),
                ],
                environment,
            )
            restore_url = URL.create(
                "postgresql+psycopg",
                username="money_acceptance",
                database="money_restore",
                query={"host": str(sockets)},
            ).render_as_string(hide_password=False)
            verify_restored(restore_url, expected)
            report["steps"].append(
                {"requirement": phase, "status": "VERIFIED", "packet_hash": expected["packet_hash"]}
            )
            report["status"] = "VERIFIED"
        except AcceptanceFailure as error:
            report.update(
                status="BLOCKED_ENVIRONMENT" if error.blocked else "FAILED",
                code=error.code,
                phase=phase,
            )
        except Exception as error:  # noqa: BLE001 - never expose source diagnostics/credentials
            report.update(
                status="FAILED",
                code="ACCEPTANCE_ASSERTION_FAILED",
                failure_class=type(error).__name__,
                phase=phase,
            )
        finally:
            if start_attempted:
                stopped = stop_owned_cluster(data, environment)
            if not stopped:
                report.update(
                    status="FAILED", code="POSTGRESQL_SHUTDOWN_FAILED", retained_directory=str(base)
                )
    if stopped:
        temporary_directory.cleanup()
    report["temporary_data_removed"] = stopped
    return report


def main() -> int:
    parser = SafeArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Run drill only; explicitly mark existing PostgreSQL integration suite NOT_RUN",
    )
    arguments = parser.parse_args()
    try:
        report = run_acceptance(run_tests=not arguments.skip_tests)
    except AcceptanceFailure as error:
        report = {"status": "FAILED", "code": error.code, "production_qualified": False}
    print(json.dumps(report, sort_keys=True))
    return (
        0
        if report["status"] == "VERIFIED"
        else 2
        if report["status"] == "BLOCKED_ENVIRONMENT"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
