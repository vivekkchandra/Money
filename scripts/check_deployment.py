"""Offline deployment-boundary check; no cloud access or credentials required."""

import ast
import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_MODULES = {"alpaca", "alpaca_trade_api", "ib_insync", "ibapi", "ccxt", "trading212"}
FORBIDDEN_CAPABILITIES = {
    "placeorder", "submitorder", "createorder", "modifyorder", "cancelorder", "cancelallorders",
    "marketorder", "limitorder", "stoporder", "stoplimitorder", "setholdings", "liquidate",
    "rebalance", "autorebalance", "getaccountbalance", "getpositions", "getportfolio",
    "getorderhistory", "buy", "sell",
}


def forbidden_python_capabilities(source: str) -> list[str]:
    """Inspect actual definitions/imports/calls, ignoring prose and docstrings."""
    violations = []
    tree = ast.parse(source)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                names = [node.func.attr]
            elif isinstance(node.func, ast.Name):
                names = [node.func.id]
            if isinstance(node.func, ast.Name) and node.func.id in {"getattr", "setattr"}:
                names += [arg.value for arg in node.args[1:2] if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and re.search(r"/(?:orders|positions|portfolio|account/balance)(?:[/?]|$)", arg.value):
                    violations.append(f"line {node.lineno}: broker resource path")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            imports = [alias.name for alias in node.names]
            names += imports
            modules = imports if isinstance(node, ast.Import) else [node.module or ""]
            if any(module.split(".")[0] in FORBIDDEN_MODULES for module in modules):
                violations.append(f"line {node.lineno}: broker SDK import")
        for name in names:
            if name.replace("_", "").lower() in FORBIDDEN_CAPABILITIES:
                violations.append(f"line {node.lineno}: forbidden execution capability {name}")
    return violations


def secret_findings(source: str) -> bool:
    patterns = (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"\bAKIA[A-Z0-9]{16}\b",
        r"\bgh[pousr]_[A-Za-z0-9]{30,}\b",
        r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}\b",
    )
    return any(re.search(pattern, source) for pattern in patterns)


def check_netlify_configuration(configuration: dict) -> None:
    """Keep paths relative to the app base and prevent static-SPA deployment drift."""
    expected = {"base": "apps/web", "command": "npm run build", "publish": ".next"}
    for key, value in expected.items():
        assert configuration["build"].get(key) == value, f"Netlify {key} must be {value}"
    plugins = [plugin.get("package") for plugin in configuration.get("plugins", [])]
    assert plugins.count("@netlify/plugin-nextjs") == 1, "Explicit Next.js SSR adapter required"
    guard = "./netlify/plugins/money-ssr-guard"
    assert plugins.count(guard) == 1, "Netlify SSR artifact guard required"
    assert plugins.index(guard) > plugins.index("@netlify/plugin-nextjs"), "SSR guard must follow adapter"
    for name, context in configuration.get("context", {}).items():
        for key, value in expected.items():
            assert key not in context or context[key] == value, f"Netlify {name} overrides {key}"
    for redirect in configuration.get("redirects", []):
        assert not (
            redirect.get("from") == "/*" and redirect.get("to") == "/index.html"
        ), "Next.js SSR cannot be replaced with a static SPA rewrite"


def main() -> None:
    configuration = tomllib.loads((ROOT / "netlify.toml").read_text())
    check_netlify_configuration(configuration)
    assert configuration["context"]["production"]["environment"]["MONEY_ENV"] == "production"
    assert configuration["context"]["deploy-preview"]["environment"]["MONEY_ENV"] == "preview"
    app = ROOT / "apps/web"
    package = json.loads((app / "package.json").read_text())
    assert {"dev", "build", "lint", "typecheck", "test", "test:browser"} <= package["scripts"].keys()
    assert (app / "package-lock.json").exists(), "Web dependency lock missing"
    lock = json.loads((app / "package-lock.json").read_text())
    adapter = package.get("devDependencies", {}).get("@netlify/plugin-nextjs", "")
    assert re.fullmatch(r"5\.\d+\.\d+", adapter), "Reviewed exact Next.js adapter version required"
    assert lock["packages"]["node_modules/@netlify/plugin-nextjs"]["version"] == adapter
    for name in ("index.js", "manifest.yml"):
        assert (app / "netlify/plugins/money-ssr-guard" / name).is_file(), "Local SSR guard missing"
    assert (app / "app/[[...view]]/page.tsx").exists(), "SSR root route missing"
    assert not re.search(r"output\s*:\s*['\"]export['\"]", (app / "next.config.ts").read_text()), "Money requires SSR and server route handlers"
    for duplicate in (app / "netlify.toml", app / "apps/web", app / "public/_redirects"):
        assert not duplicate.exists(), f"Unexpected shadow deployment configuration: {duplicate}"
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
        assert not re.search(r"(?:from\s*|import\s*\(|require\s*\()\s*[\"'](?:node:)?(?:child_process|tradingagents|hedge_fund|qlib|crewai|lean|rdagent)(?:[/'\"])", source), file
        assert not secret_findings(source), f"Possible credential in {file}"
    for file in (ROOT / "src/money/api").glob("*.py"):
        for node in ast.walk(ast.parse(file.read_text())):
            modules = [node.module or ""] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            assert not any(module.startswith(
                (
                    "money.flows",
                    "money.worker",
                    "crewai",
                    "qlib",
                    "tradingagents",
                    "hedge_fund",
                )
            ) for module in modules), file
    for file in (ROOT / "src/money").rglob("*.py"):
        source = file.read_text()
        findings = forbidden_python_capabilities(source)
        assert not findings, f"{file}: {findings}"
        assert not secret_findings(source), f"Possible credential in {file}"
    # Built browser assets must never contain configured server credential values.
    import os

    credentials = [os.environ[name] for name in ("RESEARCH_API_TOKEN", "SESSION_SECRET", "MONEY_WEB_PASSWORD") if len(os.environ.get(name, "")) >= 16]
    for file in (app / ".next/static").rglob("*.js"):
        source = file.read_text()
        assert not any(value in source for value in credentials), f"Server credential in {file}"
    assert (ROOT / "migrations/versions").is_dir()
    docker = (ROOT / "Dockerfile").read_text()
    assert "uv sync --locked" in docker and "USER money" in docker
    assert "HEALTHCHECK" in docker and "--no-editable" in docker
    assert "money.worker" in (ROOT / "docker-compose.yml").read_text()
    print(
        "Deployment boundaries verified: Netlify web, durable API, separate worker, locked builds, no broker execution or client credentials."
    )


if __name__ == "__main__":
    main()
