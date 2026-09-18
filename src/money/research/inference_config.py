"""Credential-free inference selections, explicit authentication and config loading."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from contextlib import ExitStack
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from money.research.inference import (
    Authentication,
    EndpointScope,
    HTTPInference,
    InferenceConfiguration,
    inference_endpoint,
    strict_response_json,
    validate_reasoning_effort,
)
from money.schemas.contracts import Contract

INFERENCE_ROLES = ("tradingagents", "ai_hedge_fund", "crewai")
DEFAULT_INFERENCE_CONFIG = "data/configuration/live-inference.json"


class InferenceSelection(Contract):
    """Non-secret selection; credentials are resolved only when constructing transport."""

    provider: str = Field(min_length=1, max_length=128)
    model: str = Field(min_length=1, max_length=200)
    endpoint: str
    credential_environment_variable: str | None = Field(
        default=None, pattern=r"^[A-Z][A-Z0-9_]{2,80}$"
    )
    protocol: Literal["openai-compatible", "anthropic"] = "openai-compatible"
    authentication: Authentication | None = None
    endpoint_scope: EndpointScope = "public"
    temperature: float | None = Field(default=0, ge=0, le=2)
    reasoning_effort: str | None = None
    max_output_tokens: int = Field(default=3000, ge=256, le=16000)
    maximum_prompt_bytes: int = Field(default=30000, ge=1000, le=200000)
    timeout_seconds: int = Field(default=60, ge=1, le=180)
    input_gbp_per_million: Decimal | None = Field(default=None, ge=0)
    output_gbp_per_million: Decimal | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_selection(self) -> InferenceSelection:
        if self.authentication is None:
            object.__setattr__(
                self, "authentication", "api-key" if self.protocol == "anthropic" else "bearer"
            )
        if self.authentication == "none":
            if self.credential_environment_variable is not None:
                raise ValueError("INFERENCE_UNUSED_CREDENTIAL_DENIED")
            if self.endpoint_scope != "local":
                raise ValueError("INFERENCE_UNAUTHENTICATED_PUBLIC_ENDPOINT_DENIED")
        elif self.credential_environment_variable is None:
            raise ValueError("INFERENCE_CREDENTIAL_VARIABLE_REQUIRED")
        inference_endpoint(self.endpoint, self.endpoint_scope)
        validate_reasoning_effort(self.provider, self.protocol, self.reasoning_effort)
        return self

    @property
    def is_local(self) -> bool:
        return self.endpoint_scope == "local"

    def require_hosted(self) -> None:
        """Never admit local inference as hosted-production qualification."""
        if self.is_local:
            raise ValueError("HOSTED_LOCAL_INFERENCE_DENIED")
        inference_endpoint(self.endpoint, "public")

    def inference(
        self, environ: Mapping[str, str] | None = None, **bounded_overrides: Any
    ) -> HTTPInference:
        """Construct the shared transport without reading any unrelated secret."""
        if set(bounded_overrides) - {
            "max_output_tokens",
            "maximum_prompt_bytes",
            "timeout_seconds",
        }:
            raise ValueError("INFERENCE_OVERRIDE_DENIED")
        selected = InferenceSelection.model_validate(self.model_dump() | bounded_overrides)
        source = os.environ if environ is None else environ
        secret = None
        if selected.authentication != "none":
            assert selected.credential_environment_variable is not None
            secret = source.get(selected.credential_environment_variable)
            if not secret:
                raise ValueError("INFERENCE_CREDENTIAL_MISSING")
        return HTTPInference(
            InferenceConfiguration(
                **selected.model_dump(exclude={"credential_environment_variable"}), api_key=secret
            )
        )


def load_inference_selections(
    repo: Path, environ: Mapping[str, str]
) -> dict[str, InferenceSelection]:
    """Load exactly three roles; an explicit invalid override never falls back.

    Relative paths are repository-relative, not working-directory-relative.
    Descriptor-relative reads reject symlinks and special files in every component.
    """
    configured = environ.get("MONEY_INFERENCE_CONFIG", DEFAULT_INFERENCE_CONFIG)
    if not configured or "\\" in configured or any(ord(c) < 32 for c in configured):
        raise ValueError("INFERENCE_CONFIG_INVALID")
    path = Path(configured)
    if ".." in path.parts:
        raise ValueError("INFERENCE_CONFIG_INVALID")
    if not path.is_absolute():
        path = repo.absolute() / path
    try:
        with ExitStack() as stack:
            descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            for part in path.parts[1:-1]:
                descriptor = os.open(
                    part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
                )
                stack.callback(os.close, descriptor)
            file_descriptor = os.open(
                path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor
            )
            stream = stack.enter_context(os.fdopen(file_descriptor, "rb"))
            attributes = os.fstat(stream.fileno())
            if not stat.S_ISREG(attributes.st_mode) or attributes.st_size > 100000:
                raise ValueError("INFERENCE_CONFIG_INVALID")
            raw = stream.read(100001)
            if len(raw) > 100000:
                raise ValueError("INFERENCE_CONFIG_INVALID")
        value = strict_response_json(raw)
        if set(value) != set(INFERENCE_ROLES):
            raise ValueError("INFERENCE_CONFIG_ROLES_INVALID")
        return {role: InferenceSelection.model_validate(value[role]) for role in INFERENCE_ROLES}
    except (OSError, ValueError):
        # Do not echo raw JSON or a URL that may have contained an operator secret.
        raise ValueError("INFERENCE_CONFIG_INVALID") from None
