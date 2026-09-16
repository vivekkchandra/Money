"""Opt-in genuine native execution, never satisfied by fixture substitution.

Set MONEY_RUN_PRODUCTION_INTEGRATION=1 to permit paid inference. Every missing
credential/runtime is reported as an explicit skip. A successful smoke proves
execution, not the data/model/host qualification acceptance gates.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

from money.adapters.native import NativeRunSettings
from money.adapters.native_process import BoundedNativeRunner, NativeProcessPolicy
from money.adapters.native_qlib import QlibNativeRunner, load_qualified_model
from money.adapters.native_qualitative import AIHedgeFundNativeRunner, TradingAgentsNativeRunner
from money.adapters.upstream import (
    AIHedgeFundAdapter,
    LeanAdapter,
    QlibAdapter,
    TradingAgentsAdapter,
)
from money.backtest.lean import (
    LeanContainerRunner,
    LeanContainerSettings,
    LeanCostAssumptions,
    LeanStudyParameters,
    LeanStudyQualification,
)
from money.crews.cio import CIOResult, CrewAINativeRunner
from money.research.inference import HTTPInference, InferenceConfiguration
from money.schemas.contracts import (
    AIHedgeFundResearchReport,
    FirmReport,
    LeanValidationReport,
    QlibQuantResearchReport,
    ResearchMandate,
    ResearchSnapshot,
    TradingAgentsResearchReport,
)


@pytest.fixture(autouse=True)
def opt_in() -> None:
    if os.getenv("MONEY_RUN_PRODUCTION_INTEGRATION") != "1":
        pytest.skip("SKIPPED_MISSING_CREDENTIAL: explicit paid production-integration opt-in absent")


def _file(variable: str) -> Path:
    value = os.getenv(variable)
    if not value:
        pytest.skip(f"SKIPPED_MISSING_CREDENTIAL: {variable} not configured")
    path = Path(value)
    if not path.is_file():
        pytest.skip(f"SKIPPED_EXTERNAL_UNAVAILABLE: {variable} file unavailable")
    return path


@pytest.fixture
def live_snapshot() -> ResearchSnapshot:
    snapshot = ResearchSnapshot.model_validate_json(_file("MONEY_NATIVE_QUALIFICATION_SNAPSHOT").read_bytes())
    assert snapshot.instrument.provider != "money-demo"
    assert all(record.provider != "money-demo" for record in snapshot.evidence)
    return snapshot


def _inference() -> HTTPInference:
    key = os.getenv("MONEY_NATIVE_INFERENCE_API_KEY")
    if not key:
        pytest.skip("SKIPPED_MISSING_CREDENTIAL: MONEY_NATIVE_INFERENCE_API_KEY")
    config = json.loads(_file("MONEY_NATIVE_INFERENCE_CONFIG").read_bytes())
    return HTTPInference(InferenceConfiguration(**config, api_key=key))


def _runtime(package: str) -> None:
    if importlib.util.find_spec(package) is None:
        pytest.skip(f"SKIPPED_EXTERNAL_UNAVAILABLE: pinned {package} runtime is not installed")


@pytest.mark.parametrize("firm,package", [("tradingagents", "tradingagents"), ("ai_hedge_fund", "hedge_fund")])
def test_native_qualitative_live(firm: str, package: str, live_snapshot: ResearchSnapshot) -> None:
    _runtime(package)
    inference = _inference()
    runner_type, adapter_type, report_type = (
        (TradingAgentsNativeRunner, TradingAgentsAdapter, TradingAgentsResearchReport)
        if firm == "tradingagents" else (AIHedgeFundNativeRunner, AIHedgeFundAdapter, AIHedgeFundResearchReport)
    )
    bounded = BoundedNativeRunner(runner_type(inference, NativeRunSettings(timeout_seconds=600)),
        report_type, NativeProcessPolicy(gateway_hosts=inference.allowed_network_hosts, timeout_seconds=600))
    report = adapter_type(bounded).research(ResearchMandate(), live_snapshot)
    assert report.runtime == "live" and report.claims and report.usage.input_tokens is not None


def test_qlib_native_live(live_snapshot: ResearchSnapshot) -> None:
    _runtime("qlib")
    expected = os.getenv("MONEY_QLIB_ARTIFACT_HASH")
    if not expected:
        pytest.skip("SKIPPED_MISSING_CREDENTIAL: MONEY_QLIB_ARTIFACT_HASH registry selection")
    model = load_qualified_model(_file("MONEY_QLIB_QUALIFIED_MODEL"), expected)
    runner = BoundedNativeRunner(QlibNativeRunner(model), QlibQuantResearchReport, NativeProcessPolicy())
    result = QlibAdapter(runner).research(ResearchMandate(), live_snapshot)
    assert result.prediction_score is not None and result.validation_metadata


def _reports() -> tuple[FirmReport, ...]:
    raw = json.loads(_file("MONEY_NATIVE_QUALIFICATION_REPORTS").read_bytes())
    types = {"tradingagents": TradingAgentsResearchReport,
             "ai_hedge_fund": AIHedgeFundResearchReport, "qlib": QlibQuantResearchReport}
    return tuple(types[item["firm"]].model_validate(item) for item in raw)


def test_lean_native_live(live_snapshot: ResearchSnapshot) -> None:
    config = json.loads(_file("MONEY_LEAN_QUALIFICATION_CONFIG").read_bytes())
    runner = LeanContainerRunner(LeanContainerSettings.model_validate(config["container"]),
        LeanCostAssumptions.model_validate(config["costs"]),
        LeanStudyParameters.model_validate(config["parameters"]),
        LeanStudyQualification.model_validate(config["qualification"]))
    result = LeanAdapter(runner).validate(live_snapshot, _reports())
    assert result.observations >= 30
    assert result.walk_forward and result.out_of_sample and result.pit_safe
    assert result.survivorship_checked and result.costs_included and result.sensitivity_checked


def test_crewai_native_live(live_snapshot: ResearchSnapshot) -> None:
    _runtime("crewai")
    inference = _inference()
    lean = LeanValidationReport.model_validate_json(_file("MONEY_NATIVE_QUALIFICATION_LEAN_REPORT").read_bytes())
    runner = BoundedNativeRunner(CrewAINativeRunner(inference, NativeRunSettings(timeout_seconds=600)),
        CIOResult, NativeProcessPolicy(gateway_hosts=inference.allowed_network_hosts, timeout_seconds=600))
    result = runner(live_snapshot, _reports(), lean)
    assert result.audit.completed and result.audit.findings
    assert result.usage.input_tokens is not None
