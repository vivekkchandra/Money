"""Bounded Stripe REST adapter and raw-body webhook authentication.

No browser-supplied plan, redirect or payment claim grants an entitlement.
Webhook events enqueue reconciliation; the worker retrieves current Stripe state.
"""

import hashlib
import hmac
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from money.product.settings import ProductSettings


class BillingUnavailable(RuntimeError):
    """A safe boundary error; no provider response text escapes."""


def authenticate_event(raw: bytes, signature: str, settings: ProductSettings) -> dict[str, Any]:
    if not settings.money_billing_enabled or not settings.money_stripe_webhook_secret:
        raise BillingUnavailable("BILLING_UNAVAILABLE")
    if len(raw) > 262144 or len(signature) > 2048:
        raise ValueError("INVALID_WEBHOOK")
    parts = [part.split("=", 1) for part in signature.split(",") if "=" in part]
    timestamps = [value for key, value in parts if key == "t"]
    if len(timestamps) != 1 or not re.fullmatch(r"[0-9]{10,12}", timestamps[0]):
        raise ValueError("INVALID_WEBHOOK")
    timestamp = timestamps[0]
    if abs(time.time() - int(timestamp)) > 300:
        raise ValueError("EXPIRED_WEBHOOK")
    expected = hmac.new(
        settings.money_stripe_webhook_secret.get_secret_value().encode(),
        timestamp.encode() + b"." + raw,
        hashlib.sha256,
    ).hexdigest()
    if not any(hmac.compare_digest(value, expected) for key, value in parts if key == "v1"):
        raise ValueError("INVALID_WEBHOOK")
    try:
        event = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise ValueError("INVALID_WEBHOOK") from error
    if not isinstance(event, dict) or not re.fullmatch(
        r"evt_[A-Za-z0-9_]{1,120}", str(event.get("id", ""))
    ):
        raise ValueError("INVALID_WEBHOOK")
    if event.get("livemode") is not settings.money_stripe_live:
        raise ValueError("WEBHOOK_MODE_MISMATCH")
    return event


class StripeGateway:
    def __init__(self, settings: ProductSettings) -> None:
        self.settings = settings

    def request(
        self, method: str, path: str, data: dict[str, str] | None = None, key: str | None = None
    ) -> dict[str, Any]:
        settings = self.settings
        if not settings.money_billing_enabled or not settings.money_stripe_secret_key:
            raise BillingUnavailable("BILLING_UNAVAILABLE")
        if not re.fullmatch(
            r"/(customers|checkout/sessions|billing_portal/sessions|subscriptions/sub_[A-Za-z0-9_]+)",
            path,
        ):
            raise ValueError("INVALID_BILLING_OPERATION")
        headers = {
            "Authorization": f"Bearer {settings.money_stripe_secret_key.get_secret_value()}",
            "Stripe-Version": settings.money_stripe_api_version,
        }
        if key:
            headers["Idempotency-Key"] = key
        try:
            deadline = time.monotonic() + 15
            with httpx.Client(
                timeout=httpx.Timeout(10, connect=3), follow_redirects=False, trust_env=False
            ) as client:
                with client.stream(
                    method, "https://api.stripe.com/v1" + path, headers=headers, data=data
                ) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise BillingUnavailable("BILLING_PROVIDER_UNAVAILABLE")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            raise BillingUnavailable("BILLING_RESPONSE_TIMEOUT")
                        content.extend(chunk)
                        if len(content) > 262144:
                            raise BillingUnavailable("BILLING_RESPONSE_TOO_LARGE")
                    result = json.loads(content)
                    if not isinstance(result, dict):
                        raise BillingUnavailable("BILLING_RESPONSE_INVALID")
                    return result
        except (httpx.HTTPError, ValueError) as error:
            raise BillingUnavailable("BILLING_PROVIDER_UNAVAILABLE") from error

    @staticmethod
    def hosted_url(result: dict[str, Any], host: str) -> str:
        url = result.get("url")
        if not isinstance(url, str) or len(url) > 8192:
            raise BillingUnavailable("BILLING_RESPONSE_INVALID")
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != host
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
        ):
            raise BillingUnavailable("BILLING_RESPONSE_INVALID")
        return url
