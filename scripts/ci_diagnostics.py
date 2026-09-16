"""Bounded public CI summaries: identifiers only, never error bodies or secrets."""

import argparse
import json
import re
from pathlib import Path
from xml.etree import ElementTree

MAX_BYTES = 8_000_000
ROOT = Path(__file__).resolve().parents[1]
# Finite vocabularies are intentional. Uppercase alone does not make an API key
# or a provider-controlled message safe to publish in public job metadata.
MONEY_CODES = frozenset(
    {
        "RESEARCH_FAILED",
        "FIRST_PASS_FAILED",
        "FIRST_PASS_INCOMPLETE",
        "FIRST_PASS_SNAPSHOT_MISMATCH",
        "LIVE_CONFIGURATION_UNAVAILABLE",
        "WORKER_INCOMPLETE",
        "WORKER_INTERRUPTED",
        "WORKER_LEASE_EXPIRED",
        "JOB_TIMEOUT",
        "JOB_RETRY_EXHAUSTED",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_TIMEOUT",
        "PROVIDER_COVERAGE_MISSING",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_BUSY",
        "MARKET_DATA_UNAVAILABLE",
        "CRITICAL_DATA_MISSING",
        "CRITICAL_DATA_STALE",
        "CRITICAL_DATA_CONFLICT",
        "PIT_VIOLATION",
        "ELIGIBILITY_UNKNOWN",
        "ELIGIBILITY_STALE",
        "INSTRUMENT_NOT_FOUND",
        "STALE_DATA",
        "QLIB_MODEL_UNAVAILABLE",
        "LEAN_TIMEOUT",
        "LEAN_FAILED",
        "LEAN_INSUFFICIENT_EVIDENCE",
        "CREWAI_FAILED",
        "AUDIT_INCOMPLETE",
        "RED_TEAM_VETO",
        "CONSENSUS_INSUFFICIENT",
        "SYNTHETIC_DEMO_DISABLED",
        "RESEARCH_CONFIGURATION_CHANGED",
        "COMMAND_FAILED",
        "COMMAND_TIMEOUT",
        "EXECUTABLE_UNAVAILABLE",
        "ENVIRONMENT_PERMISSION_DENIED",
        "POSTGRESQL_TOOLS_MISSING",
        "CHECKPOINT_TIMEOUT",
        "LEASE_RECOVERY_TIMEOUT",
        "STALE_WORKER_NOT_FENCED",
        "RESTORED_IMMUTABILITY_MISSING",
    }
)
STAGES = frozenset(
    {
        "QUEUED",
        "ELIGIBILITY_CHECK",
        "DISCOVERY",
        "SNAPSHOT_BUILD",
        "FIRST_PASS_RESEARCH",
        "FIRST_PASS_LOCKED",
        "LEAN_VALIDATION",
        "CREWAI_AUDIT",
        "CROSS_EXAMINATION",
        "CONSENSUS",
        "COMPLETE",
        "REJECTED",
        "FAILED",
    }
)
PHASES = frozenset(
    {
        "postgres_init",
        "postgres_start",
        "migrations",
        "postgresql_integration_tests",
        "worker_crash_recovery",
        "backup",
        "postgres_restart",
        "restore_empty_database",
    }
)
EXCEPTIONS = frozenset(
    {
        "AssertionError",
        "ValueError",
        "TypeError",
        "KeyError",
        "RuntimeError",
        "TimeoutError",
        "PermissionError",
        "ValidationError",
        "DatabaseError",
        "IntegrityError",
        "OperationalError",
        "StoreError",
        "LeaseLost",
        "BarrierNotLocked",
        "InvalidUpstreamReport",
        "ProviderFailure",
        "SourceSecurityError",
        "PackageNotFoundError",
        "ModuleNotFoundError",
        "ImportError",
    }
)
SMOKE_STAGES = {
    "allocate local listener ports": "ALLOCATE_LISTENERS",
    "allocate local listeners": "ALLOCATE_LISTENERS",
    "database migration": "DATABASE_MIGRATION",
    "migrate isolated account/research database": "DATABASE_MIGRATION",
    "start API and Next.js services": "START_SERVICES",
    "start API and Next": "START_SERVICES",
    "unauthenticated route, asset and security-header acceptance": "WEB_ACCEPTANCE",
    "durable workspace login": "LOGIN",
    "authenticated company search and durable research enqueue": "RESEARCH_ENQUEUE",
    "separate research worker": "RESEARCH_WORKER",
    "separate worker": "RESEARCH_WORKER",
    "persisted packet retrieval": "PACKET_RETRIEVAL",
    "sealed reports and evidence retrieval": "EVIDENCE_RETRIEVAL",
    "SSR research page and per-request CSP nonce": "SSR_SECURITY",
    "durable logout revocation": "LOGOUT",
    "Playwright browser acceptance": "BROWSER_ACCEPTANCE",
    "web remains usable after backend shutdown": "BACKEND_OUTAGE",
    "signup, encrypted verification outbox and login": "SIGNUP_VERIFICATION_LOGIN",
    "workspace creation and membership selection": "WORKSPACE_CREATION",
    "entitlement and durable research": "RESEARCH_ENTITLEMENT",
    "authenticated company selection": "COMPANY_SELECTION",
    "immutable result and usage": "PACKET_AND_USAGE",
    "browser customer journey": "BROWSER_ACCEPTANCE",
}


def _location(path: str, line: str, exception: str | None = None) -> str | None:
    """Return only an existing source location inside this checkout, never a path prefix."""
    if path.startswith("file://"):
        path = path[7:]
    if path.startswith("money/"):
        path = "src/" + path
    selected = Path(path)
    if selected.is_absolute():
        try:
            selected = selected.relative_to(ROOT)
        except ValueError:
            return None
    relative = selected.as_posix()
    if (
        not re.fullmatch(
            r"(?:src/money|tests|scripts|apps/web/scripts)/(?:[A-Za-z0-9_]+/)*"
            r"[A-Za-z0-9_-]+\.(?:py|mjs|js|ts)",
            relative,
        )
        or ".." in selected.parts
    ):
        return None
    try:
        source = ROOT / selected
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(ROOT):
            return None
    except OSError:
        return None
    if not re.fullmatch(r"[1-9][0-9]{0,5}", line):
        return None
    suffix = f": {exception}" if exception in EXCEPTIONS else ""
    return f"{relative}:{line}{suffix}"


def _safe_details(text: str) -> list[str]:
    """Extract locations/codes/stages; never serialize arbitrary log or assertion content."""
    locations = []
    # pytest's final line can include an exception message. Capture the location
    # and a known exception type, but NEVER the remainder of that line.
    for match in re.finditer(
        r"(?m)^[ \t]*(?P<path>[^\s:\"'<>]+\.py):(?P<line>[1-9][0-9]{0,5})"
        r":[ \t]*(?P<exception>[A-Za-z_][A-Za-z0-9_]*)(?::[^\r\n]*)?[ \t]*$",
        text,
    ):
        found = _location(match["path"], match["line"], match["exception"])
        if found:
            locations.append(found)
    # Standard Python traceback frames and Node's checkout-owned file:// frames.
    for match in re.finditer(r'File "([^"\r\n]+\.py)", line ([1-9][0-9]{0,5})', text):
        found = _location(match[1], match[2])
        if found:
            locations.append(found)
    for match in re.finditer(
        r"file://([^\s()\"'<>]+\.(?:mjs|js|ts)):([1-9][0-9]{0,5}):[0-9]+", text
    ):
        found = _location(match[1], match[2])
        if found:
            locations.append(found)
    for match in re.finditer(
        r"""["']error_location["']\s*:\s*["']([^\r\n"']+\.py):([1-9][0-9]{0,5})["']""", text
    ):
        found = _location(match[1], match[2])
        if found:
            locations.append(found)
    codes = sorted(set(re.findall(r"\b[A-Z][A-Z0-9_]{2,79}\b", text)) & MONEY_CODES)
    stages = sorted(
        {
            match[1]
            for match in re.finditer(
                r"""["'](?:stage|current_stage|status)["']\s*:\s*["']([A-Z_]{1,50})["']""", text
            )
        }
        & STAGES
    )
    return [
        *dict.fromkeys(locations),
        *(f"code={code}" for code in codes[:4]),
        *(f"stage={stage}" for stage in stages[:3]),
    ]


def _bounded_summary(counts: str, names: list[str], details: list[str]) -> str:
    # Keep the first failed test and useful diagnostics ahead of long name lists.
    entries = list(dict.fromkeys([*names[:1], *details, *names[1:]]))
    result = counts
    for entry in entries:
        separator = "; " if result == counts else ", "
        if len(result) + len(separator) + len(entry) <= 500:
            result += separator + entry
    return result


def summarize(path: Path, kind: str) -> str:
    """Summarize test/advisory identities without copying untrusted log text."""
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
            return "REPORT_UNAVAILABLE"
        with path.open("rb") as report_file:
            raw = report_file.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return "REPORT_UNAVAILABLE"
    except OSError:
        return "REPORT_UNAVAILABLE"
    try:
        if kind == "pytest":
            # CI produces UTF-8 JUnit. Reject alternative encodings/NULs so an
            # encoded DTD cannot evade the explicit entity prohibition.
            decoded = raw.decode("utf-8-sig")
            if "\x00" in decoded or "<!DOCTYPE" in decoded.upper() or "<!ENTITY" in decoded.upper():
                return "REPORT_INVALID"
            root = ElementTree.fromstring(decoded)
            if root.tag not in {"testsuites", "testsuite"}:
                return "REPORT_INVALID"
            failed = [
                item.get("name", "unknown").split("[", 1)[0]
                for item in root.iter("testcase")
                if item.find("failure") is not None or item.find("error") is not None
            ]
            counts = f"{len(list(root.iter('testcase')))} cases; {len(failed)} failed"
            # Parameter values and assertion bodies may contain credentials.
            names = [name for name in failed if re.fullmatch(r"test_[a-zA-Z0-9_]{1,100}", name)]
            details = []
            for item in root.iter("testcase"):
                if item.find("failure") is None and item.find("error") is None:
                    continue
                for tag in ("failure", "error"):
                    failure = item.find(tag)
                    if failure is None:
                        continue
                    details.extend(_safe_details(failure.text or ""))
                for tag in ("system-out", "system-err"):
                    captured = item.find(tag)
                    if captured is not None:
                        details.extend(_safe_details(captured.text or ""))
            return _bounded_summary(counts, names, details)
        elif kind == "smoke":
            decoded = raw.decode("utf-8", errors="replace")
            stages = []
            for match in re.finditer(
                r"(?:Smoke failed during |FAILED commercial acceptance at )([^:\r\n]{1,120}):",
                decoded,
            ):
                if match[1] in SMOKE_STAGES:
                    stages.append(f"stage={SMOKE_STAGES[match[1]]}")
            # This summarizes failure evidence only, never infers a successful journey.
            details = _safe_details(decoded)
            counts = "FAILURE_DIAGNOSTICS" if stages or details else "NO_SAFE_FAILURE_DIAGNOSTICS"
            return _bounded_summary(counts, [], [*stages, *details])
        elif kind == "acceptance":
            report = json.loads(raw)
            status = report.get("status")
            if status not in {"VERIFIED", "FAILED", "BLOCKED_ENVIRONMENT"}:
                return "REPORT_INVALID"
            parts = [status]
            for key, permitted in (
                ("phase", PHASES),
                ("code", MONEY_CODES),
                ("failure_class", EXCEPTIONS),
            ):
                value = report.get(key)
                if isinstance(value, str) and value in permitted:
                    parts.append(f"{key}={value}")
            return "; ".join(parts)
        else:
            report = json.loads(raw)
            dependencies = report.get("dependencies", [])
            names = []
            for dependency in dependencies:
                package = dependency.get("name", "")
                if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", package):
                    continue
                for vulnerability in dependency.get("vulns", []):
                    identifier = vulnerability.get("id", "")
                    if re.fullmatch(r"(?:CVE|GHSA|PYSEC)-[A-Za-z0-9-]{1,60}", identifier):
                        names.append(f"{package}:{identifier}")
            counts = f"{len(names)} advisory findings; {len(dependencies)} dependency records"
        return (counts + ("; " + ", ".join(dict.fromkeys(names)) if names else ""))[:500]
    except (ValueError, TypeError, AttributeError, ElementTree.ParseError):
        return "REPORT_INVALID"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("pytest", "audit", "acceptance", "smoke"))
    parser.add_argument("report", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    summary = summarize(args.report, args.kind)
    print(summary)
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write(f"summary={summary}\n")


if __name__ == "__main__":
    main()
