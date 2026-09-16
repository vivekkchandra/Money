from dataclasses import replace
from decimal import Decimal

import pytest

from money.research.inference import HTTPInference, InferenceConfiguration, strict_response_json


def config(**kwargs):
    return InferenceConfiguration(
        provider="explicit-provider",
        model="exact-version",
        endpoint="https://inference.example.test/v1/chat/completions",
        api_key="test-only-key",
        **kwargs,
    )


class Inference(HTTPInference):
    def __init__(self, configuration, response):
        super().__init__(configuration)
        self.response = response
        self.requests = []

    def _post(self, body, headers):
        self.requests.append(body)
        return self.response


def response(**kwargs):
    return dict(
        model="exact-version",
        choices=[{"finish_reason": "stop", "message": {"content": '{"facts":[]}'}}],
        **kwargs,
    )


def test_explicit_provider_and_actual_usage_cost_unknown():
    provider = Inference(config(), response(usage={"prompt_tokens": 20, "completion_tokens": 10}))
    assert provider.complete("policy", "untrusted evidence") == '{"facts":[]}'
    assert provider.usage().input_tokens == 20
    assert provider.usage().cost_gbp is None
    assert provider.requests[0]["model"] == "exact-version"
    assert provider.configuration.api_key not in repr(provider.configuration)


def test_cost_known_only_with_both_rates_and_usage():
    provider = Inference(
        config(input_gbp_per_million=Decimal(1), output_gbp_per_million=Decimal(2)),
        response(usage={"prompt_tokens": 20, "completion_tokens": 10}),
    )
    provider.complete("policy", "evidence")
    assert provider.usage().cost_gbp == Decimal("0.00004")
    provider.response = response()
    provider.complete("policy", "evidence")
    assert provider.usage().cost_gbp is None and provider.usage().input_tokens is None


@pytest.mark.parametrize(
    "result",
    [
        response() | {"model": "silently-switched-model"},
        response() | {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]},
        response()
        | {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "foo", "tool_calls": [{"name": "get_secret"}]},
                }
            ]
        },
    ],
)
def test_provider_switch_truncation_and_tool_injection_are_rejected(result):
    with pytest.raises(ValueError):
        Inference(config(), result).complete("policy", "evidence")


def test_input_bound_before_paid_call():
    provider = Inference(replace(config(), maximum_prompt_bytes=100), response())
    with pytest.raises(ValueError, match="INPUT_LIMIT"):
        provider.complete("policy", "£" * 100)
    assert provider.requests == []


def test_anthropic_protocol_has_no_native_tools_or_browser():
    provider = Inference(
        config(protocol="anthropic"),
        {
            "model": "exact-version",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "bounded answer"}],
            "usage": {"input_tokens": 11, "output_tokens": 3},
        },
    )
    assert provider.complete("policy", "evidence") == "bounded answer"
    assert provider.requests[0]["max_tokens"] == 4096
    assert "tools" not in provider.requests[0]


@pytest.mark.parametrize("value", [True, False, "20", 1.5, -1, [], {}])
def test_usage_counters_are_not_coerced(value):
    with pytest.raises(ValueError):
        Inference(config(), response(usage={"prompt_tokens": value})).complete("policy", "facts")


@pytest.mark.parametrize(
    "raw", [b'{"model":"a","model":"b"}', b'{"usage":{"x":NaN}}', b'{"x":Infinity}', b"[]"]
)
def test_ambiguous_json_is_rejected(raw):
    with pytest.raises(ValueError):
        strict_response_json(raw)


def test_truncated_paid_response_retains_actual_usage():
    provider = Inference(
        config(),
        response(usage={"prompt_tokens": 100, "completion_tokens": 50})
        | {"choices": [{"finish_reason": "length"}]},
    )
    with pytest.raises(ValueError, match="INCOMPLETE"):
        provider.complete("policy", "evidence")
    assert provider.usage().input_tokens == 100


def test_live_manifest_budget_preflight_matches_native_limits():
    from money.research.budgets import BudgetLimits
    from money.research.live import InferenceSelection, validate_invocation_budgets

    selection = InferenceSelection(
        provider="explicit",
        model="pinned",
        endpoint="https://inference.example.test/v1/chat/completions",
        credential_environment_variable="TEST_INFERENCE_KEY",
    )
    with pytest.raises(ValueError, match="BUDGET_CONFIGURATION"):
        validate_invocation_budgets((selection, selection, selection), 16, BudgetLimits())
    limits = BudgetLimits(
        per_job=2_000_000,
        per_candidate=2_000_000,
        per_stage=1_200_000,
        per_agent=600_000,
        daily=2_000_000,
    )
    validate_invocation_budgets((selection, selection, selection), 16, limits)
    with pytest.raises(ValueError, match="BUDGET_CONFIGURATION"):
        validate_invocation_budgets((selection, selection, selection), 16, limits, 24)
    with pytest.raises(ValueError, match="CHALLENGE_LIMIT"):
        validate_invocation_budgets((selection, selection, selection), 16, limits, -1)
    with pytest.raises(ValueError, match="MODEL_BUDGET_CONFIGURATION"):
        validate_invocation_budgets(
            (selection, selection, selection),
            16,
            limits.model_copy(update={"per_model": (("explicit/pinned", 10),)}),
        )
