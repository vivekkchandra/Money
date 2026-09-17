"""Small, content-free access probes; never native or hosted qualification.

The same probe is used by the operator CLI and resumable qualification runner.
An Ollama model tag is recorded exactly as returned, not described as immutable
weights. Administrative review and target-runtime qualification remain separate.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

from pydantic import AwareDatetime, Field, model_validator

from money.data.security import ProviderFailure
from money.qualification.core import assert_secret_free
from money.research.call_telemetry import InferenceReceipt, capture_calls
from money.research.inference_config import InferenceSelection, load_inference_selections
from money.schemas.contracts import Contract, content_hash

PROBE_KIND: Literal["money-inference-access-v2"] = "money-inference-access-v2"
PROBE_PROMPT = "Reply with exactly OK"
PROBE_RESPONSE_SHA256 = hashlib.sha256(b"OK").hexdigest()


class InferenceProbeEvidence(Contract):
    """Non-secret observation of one real bounded request, without report claims."""

    kind: Literal["money-inference-access-v2"] = PROBE_KIND
    role: Literal["tradingagents", "ai_hedge_fund", "crewai"]
    selection_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    verified_at: AwareDatetime
    provider: str
    selected_model: str
    endpoint: str
    protocol: str
    authentication: str
    scope: Literal["LOCAL_INFERENCE_ONLY", "REMOTE_INFERENCE_ACCESS_ONLY"]
    server_reachable: Literal[True] = True
    model_available: Literal[True] | None = None
    model_availability_checked: bool
    chat_completions_verified: Literal[True] = True
    final_content_verified: Literal[True] = True
    actual_returned_model: str
    response_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    calls: tuple[InferenceReceipt, ...] = Field(min_length=1, max_length=1)
    native_runtime_qualified: Literal[False] = False
    production_qualified: Literal[False] = False

    @model_validator(mode="after")
    def measured_identity(self) -> InferenceProbeEvidence:
        if (
            self.response_sha256 != PROBE_RESPONSE_SHA256
            or self.actual_returned_model != self.selected_model
            or any(
                call.status != "SUCCEEDED"
                or call.provider != self.provider
                or call.model != self.selected_model
                or call.provider_calls != 1
                for call in self.calls
            )
            or self.model_availability_checked != (self.model_available is True)
            or (self.provider == "ollama" and not self.model_availability_checked)
        ):
            raise ValueError("INFERENCE_PROBE_EVIDENCE_INVALID")
        return self

    def matches(self, role: str, selection: InferenceSelection) -> bool:
        return (
            self.role == role
            and self.selection_sha256 == content_hash(selection.model_dump(mode="json"))
            and self.provider == selection.provider
            and self.selected_model == selection.model
            and self.endpoint == selection.endpoint
            and self.protocol == selection.protocol
            and self.authentication == selection.authentication
            and self.scope
            == ("LOCAL_INFERENCE_ONLY" if selection.is_local else "REMOTE_INFERENCE_ACCESS_ONLY")
        )


def probe_selection(
    role: Literal["tradingagents", "ai_hedge_fund", "crewai"],
    selection: InferenceSelection,
    environ: Mapping[str, str],
    *,
    observed_at: datetime | None = None,
) -> InferenceProbeEvidence:
    """Check an exact selected model; consume only assistant final content."""
    inference = selection.inference(
        environ,
        max_output_tokens=min(512, selection.max_output_tokens),
        maximum_prompt_bytes=1000,
        timeout_seconds=min(180 if selection.is_local else 60, selection.timeout_seconds),
    )
    availability_checked = selection.provider == "ollama"
    if availability_checked and selection.model not in inference.available_models():
        raise ValueError("INFERENCE_MODEL_UNAVAILABLE")
    with capture_calls() as calls:
        response = inference.complete(PROBE_PROMPT, PROBE_PROMPT)
    if response != "OK" or inference.last_response_model is None:
        raise ValueError("INFERENCE_PROBE_CONTENT_INVALID")
    return InferenceProbeEvidence(
        role=role,
        selection_sha256=content_hash(selection.model_dump(mode="json")),
        verified_at=observed_at or datetime.now(UTC),
        provider=selection.provider,
        selected_model=selection.model,
        endpoint=selection.endpoint,
        protocol=selection.protocol,
        authentication=cast(str, selection.authentication),
        scope="LOCAL_INFERENCE_ONLY" if selection.is_local else "REMOTE_INFERENCE_ACCESS_ONLY",
        model_available=True if availability_checked else None,
        model_availability_checked=availability_checked,
        actual_returned_model=inference.last_response_model,
        response_sha256=hashlib.sha256(response.encode()).hexdigest(),
        calls=tuple(calls),
    )


def safe_probe_error(error: Exception) -> str:
    """Exceptions and provider responses may contain secrets; return codes only."""
    if isinstance(error, ProviderFailure) and error.code in {
        "PROVIDER_TIMEOUT",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_URL_REJECTED",
    }:
        return error.code
    if isinstance(error, ValueError) and str(error) in {
        "INFERENCE_MODEL_UNAVAILABLE",
        "INFERENCE_MODEL_MISMATCH",
        "INFERENCE_INCOMPLETE",
        "INFERENCE_OUTPUT_INVALID",
        "INFERENCE_RESPONSE_INVALID",
        "INFERENCE_USAGE_INVALID",
        "INFERENCE_PROBE_CONTENT_INVALID",
        "INFERENCE_CREDENTIAL_REQUIRED",
        "INFERENCE_CREDENTIAL_MISSING",
        "INFERENCE_MODEL_CATALOGUE_INVALID",
    }:
        return str(error)
    return "INFERENCE_PROBE_FAILED"


def _print_evidence(value: dict) -> bool:
    raw = json.dumps(value, sort_keys=True)
    try:
        assert_secret_free(raw.encode(), os.environ)
    except ValueError:
        print(
            json.dumps(
                {
                    "status": "INFERENCE_PROBE_BLOCKED",
                    "error": "INFERENCE_OUTPUT_UNSAFE",
                    "production_qualified": False,
                }
            )
        )
        return False
    print(raw)
    return True


def main() -> int:
    """Print machine-readable observations only; never approve a release."""
    try:
        selections = load_inference_selections(Path(__file__).resolve().parents[3], os.environ)
    except Exception:
        print(
            json.dumps(
                {
                    "status": "INFERENCE_PROBE_BLOCKED",
                    "error": "INFERENCE_CONFIGURATION_INVALID",
                    "production_qualified": False,
                }
            )
        )
        return 2
    receipts: list[dict] = []
    failures: list[dict[str, str]] = []
    for role in ("tradingagents", "ai_hedge_fund", "crewai"):
        try:
            receipts.append(
                probe_selection(
                    cast(Literal["tradingagents", "ai_hedge_fund", "crewai"], role),
                    selections[role],
                    os.environ,
                ).model_dump(mode="json")
            )
        except Exception as error:
            failures.append({"role": role, "error": safe_probe_error(error)})
    emitted = _print_evidence(
        {
            "status": "INFERENCE_PROBE_PASSED" if not failures else "INFERENCE_PROBE_BLOCKED",
            "scope": "LOCAL_INFERENCE_ONLY"
            if any(value.is_local for value in selections.values())
            else "REMOTE_INFERENCE_ACCESS_ONLY",
            "production_qualified": False,
            "selections": {
                role: {
                    "provider": value.provider,
                    "model": value.model,
                    "endpoint": value.endpoint,
                    "protocol": value.protocol,
                    "authentication": value.authentication,
                    "scope": "LOCAL_INFERENCE_ONLY"
                    if value.is_local
                    else "REMOTE_INFERENCE_ACCESS_ONLY",
                    "selection_sha256": content_hash(value.model_dump(mode="json")),
                }
                for role, value in selections.items()
            },
            "receipts": receipts,
            "failures": failures,
        }
    )
    return 2 if failures or not emitted else 0
