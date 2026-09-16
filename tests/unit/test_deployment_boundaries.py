"""Adversarial regression checks for executable brokerage capabilities."""

import runpy
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
