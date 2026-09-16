"""Offline deployment-boundary check; no cloud access or credentials required."""

import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    configuration = tomllib.loads((ROOT / "netlify.toml").read_text())
    assert configuration["build"]["base"] == "apps/web"
    assert configuration["build"]["publish"] == ".next"
    assert "python" not in configuration["build"]["command"]
    app = ROOT / "apps/web"
    package = json.loads((app / "package.json").read_text())
    assert {"dev", "build", "lint", "typecheck", "test"} <= package["scripts"].keys()
    assert (app / "package-lock.json").exists(), "Web dependency lock missing"
    source_files = [
        file for directory in ("app", "lib", "components") for file in (app / directory).rglob("*")
    ]
    assert source_files, "Web source missing"
    for file in source_files:
        if file.suffix not in {".ts", ".tsx", ".js", ".jsx"}:
            continue
        source = file.read_text()
        assert not re.search(r"NEXT_PUBLIC_\w*(?:KEY|TOKEN|SECRET|PASSWORD)", source), file
        if '"use client"' in source or "'use client'" in source:
            assert "process.env.RESEARCH_API" not in source, file
            assert "process.env.SESSION_SECRET" not in source, file
        assert not re.search(r"from\s+[\"'](?:child_process|node:child_process)", source), file
    for file in (ROOT / "src/money/api").glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            module = node.module if isinstance(node, ast.ImportFrom) else None
            assert not module or not module.startswith(
                (
                    "money.flows",
                    "money.worker",
                    "crewai",
                    "qlib",
                    "tradingagents",
                    "hedge_fund",
                )
            ), file
    assert (ROOT / "migrations/versions").is_dir()
    docker = (ROOT / "Dockerfile").read_text()
    assert "uv sync --locked" in docker and "USER money" in docker
    assert "money.worker" in (ROOT / "docker-compose.yml").read_text()
    print(
        "Deployment boundaries verified: Netlify web, durable API, separate worker, locked builds."
    )


if __name__ == "__main__":
    main()
