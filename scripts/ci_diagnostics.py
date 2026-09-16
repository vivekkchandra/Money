"""Bounded public CI summaries: identifiers only, never error bodies or secrets."""

import argparse
import json
import re
from pathlib import Path
from xml.etree import ElementTree

MAX_BYTES = 8_000_000


def summarize(path: Path, kind: str) -> str:
    """Summarize test/advisory identities without copying untrusted log text."""
    if not path.is_file() or path.is_symlink() or path.stat().st_size > MAX_BYTES:
        return "REPORT_UNAVAILABLE"
    raw = path.read_bytes()
    try:
        if kind == "pytest":
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                return "REPORT_INVALID"
            root = ElementTree.fromstring(raw)
            failed = [
                item.get("name", "unknown").split("[", 1)[0]
                for item in root.iter("testcase")
                if item.find("failure") is not None or item.find("error") is not None
            ]
            counts = f"{len(list(root.iter('testcase')))} cases; {len(failed)} failed"
            # Parameter values and assertion bodies may contain credentials.
            names = [name for name in failed if re.fullmatch(r"test_[a-zA-Z0-9_]{1,100}", name)]
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
    parser.add_argument("kind", choices=("pytest", "audit"))
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
