"""Adversarial regression checks for executable brokerage capabilities."""

import runpy
import tomllib
from copy import deepcopy
from pathlib import Path

import pytest

CHECKER = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/check_deployment.py"))


@pytest.mark.parametrize(
    "source",
    [
        "broker.place_order(ticker='ABC')",
        "from broker import place_order as make_research\nmake_research()",
        "getattr(broker, 'submit_order')()",
        "client.post('https://example.test/equity/orders')",
        "import alpaca.trading.client as source",
        "class BrokerExecutableOrder:\n    def cancel_order(self): pass",
        "self.SetHoldings('ABC', 1)",
        "broker.get_positions()",
    ],
)
def test_broker_capability_guard_rejects_executable_behavior(source: str) -> None:
    assert CHECKER["forbidden_python_capabilities"](source)


def test_broker_guard_distinguishes_research_prose_and_database_operations() -> None:
    source = '''"""Never call broker.place_order or /equity/orders."""
def research(snapshot):
    # No place_order capability is provided to this firm.
    return connection.execute(select(evidence).order_by(evidence.created_at))
'''
    assert CHECKER["forbidden_python_capabilities"](source) == []


def test_credentials_are_detected_without_committing_a_real_secret() -> None:
    assert CHECKER["secret_findings"]("sk-" + "x" * 40)
    assert CHECKER["secret_findings"]("-----BEGIN " + "PRIVATE KEY-----")
    assert not CHECKER["secret_findings"]("RESEARCH_API_TOKEN=<set in environment>")


def test_entire_money_deployment_boundary() -> None:
    CHECKER["main"]()


@pytest.mark.parametrize("change", [
    {"build": {"base": "apps/web/apps/web", "command": "npm run build", "publish": ".next"}},
    {"build": {"base": "apps/web", "command": "npm run build", "publish": "apps/web/.next"}},
    {"context": {"production": {"publish": "."}}},
    {"redirects": [{"from": "/*", "to": "/index.html", "status": 200}]},
    {"plugins": []},
    {"plugins": [{"package": "@netlify/plugin-nextjs"}, {"package": "@netlify/plugin-nextjs"}]},
    {"plugins": [{"package": "@netlify/plugin-nextjs"}]},
    {"plugins": [{"package": "./netlify/plugins/money-ssr-guard"}, {"package": "@netlify/plugin-nextjs"}]},
])
def test_netlify_path_and_ssr_contract_rejects_regressions(change):
    configuration = tomllib.loads((Path(__file__).resolve().parents[2] / "netlify.toml").read_text())
    modified = deepcopy(configuration) | change
    with pytest.raises(AssertionError):
        CHECKER["check_netlify_configuration"](modified)
