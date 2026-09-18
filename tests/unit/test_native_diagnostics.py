"""Synthetic diagnostics fixtures; never runtime qualification evidence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from money.adapters.native import NativeDeadline
from money.adapters.native_process import (
    BoundedNativeRunner,
    NativeDiagnosticError,
    NativeProcessPolicy,
    ProviderEscapeDenied,
    native_failure_code,
)
from money.adapters.native_subprocess import _exchange
from money.adapters.upstream import InvalidUpstreamReport
from money.data.security import ProviderFailure
from money.research.independent import _failure_code
from money.schemas.contracts import Contract


@pytest.mark.parametrize("error,expected", [
    (ProviderFailure("PROVIDER_TIMEOUT", retryable=True), "PROVIDER_TIMEOUT"),
    (NativeDeadline("native workflow deadline exhausted"), "NATIVE_AGENT_TIMEOUT"),
    (NativeDeadline("native inference call budget exhausted"), "NATIVE_CALL_BUDGET_EXHAUSTED"),
    (ValueError("INFERENCE_EMPTY_RESPONSE"), "INFERENCE_EMPTY_RESPONSE"),
    (ValueError("INFERENCE_INCOMPLETE"), "INFERENCE_INCOMPLETE"),
    (ValueError("INFERENCE_TOOL_OUTPUT_DENIED"), "NATIVE_TOOL_CALL_FAILED"),
    (ValueError("INFERENCE_TOOL_CALL_INVALID"), "NATIVE_TOOL_CALL_FAILED"),
    (ValueError("INFERENCE_OUTPUT_INVALID"), "INFERENCE_RESPONSE_INVALID"),
    (InvalidUpstreamReport("native structured research output is malformed"), "NATIVE_STRUCTURED_OUTPUT_INVALID"),
    (InvalidUpstreamReport("fixture report identity mismatch"), "NATIVE_REPORT_INVALID"),
    (ProviderEscapeDenied("fixture secret"), "NATIVE_CAPABILITY_DENIED"),
    (RuntimeError("fixture secret"), "NATIVE_ADAPTER_EXCEPTION"),
])
def test_failure_categories_are_fixed_and_preserved(error: Exception, expected: str) -> None:
    assert native_failure_code(error) == expected
    assert _failure_code(error) == expected
    assert "fixture" not in expected


def test_unknown_marker_or_extra_exception_arguments_are_never_echoed() -> None:
    assert native_failure_code(ValueError("INFERENCE_EMPTY_RESPONSE fixture-secret")) == "NATIVE_ADAPTER_EXCEPTION"
    assert native_failure_code(ValueError("INFERENCE_EMPTY_RESPONSE", "fixture-secret")) == "NATIVE_ADAPTER_EXCEPTION"
    with pytest.raises(ValueError, match="^NATIVE_DIAGNOSTIC_CODE_INVALID$"):
        NativeDiagnosticError("fixture-secret")


class UnusedResult(Contract):
    fixture_only: bool


@dataclass(frozen=True)
class FailureFixture:
    category: str

    def __call__(self) -> UnusedResult:
        if self.category == "model_timeout":
            raise ProviderFailure("PROVIDER_TIMEOUT", retryable=True)
        if self.category == "agent_timeout":
            raise NativeDeadline("native workflow deadline exhausted")
        if self.category == "empty":
            raise ValueError("INFERENCE_EMPTY_RESPONSE")
        if self.category == "structured":
            raise InvalidUpstreamReport("native structured research output is malformed")
        if self.category == "tool":
            raise ValueError("INFERENCE_TOOL_OUTPUT_DENIED")
        if self.category == "capability":
            raise ProviderEscapeDenied("fixture-secret-must-not-escape")
        raise RuntimeError("fixture-secret-must-not-escape")


@pytest.mark.parametrize("category,expected", [
    ("model_timeout", "PROVIDER_TIMEOUT"), ("agent_timeout", "NATIVE_AGENT_TIMEOUT"),
    ("empty", "INFERENCE_EMPTY_RESPONSE"), ("structured", "NATIVE_STRUCTURED_OUTPUT_INVALID"),
    ("tool", "NATIVE_TOOL_CALL_FAILED"), ("adapter", "NATIVE_ADAPTER_EXCEPTION"),
    ("capability", "NATIVE_CAPABILITY_DENIED"),
])
def test_real_bounded_process_preserves_only_allowlisted_diagnostics(category: str, expected: str) -> None:
    runner = BoundedNativeRunner(FailureFixture(category), UnusedResult, NativeProcessPolicy(timeout_seconds=10))
    with pytest.raises(Exception) as captured:
        runner()
    assert native_failure_code(captured.value) == expected
    assert "fixture-secret" not in str(captured.value)


def test_launch_failure_is_distinct_from_adapter_exception(tmp_path: Path) -> None:
    with pytest.raises(NativeDiagnosticError, match="^NATIVE_SUBPROCESS_FAILED$"):
        _exchange([str(tmp_path / "missing-interpreter")], b"{}", NativeProcessPolicy(), tmp_path)
