"""Support can find review requests without exposing customer mail or research."""

import json

import pytest
from fastapi import HTTPException
from test_commercial_accounts import PASSWORD, configuration, customer

from money.accounts.service import AccountService
from money.product.api import admin
from money.product.service import ProductService
from money.product.settings import ProductSettings


def test_offboarding_support_is_allowlisted_bounded_and_content_free(store):
    accounts = AccountService(store, configuration(store))
    target = customer(accounts, "delete-me@example.test")
    operator = customer(accounts, "operator@example.test")
    principal = accounts.principal(operator["session_token"], operator["workspace"]["id"])
    accounts.request_deletion(accounts.authenticated_user(target["session_token"]), PASSWORD)
    with pytest.raises(HTTPException) as denied:
        admin(ProductService(store), principal)
    assert denied.value.status_code == 403
    product = ProductService(
        store, ProductSettings(money_internal_admin_user_ids=[principal.user_id])
    )
    report = admin(product, principal)
    requests = report["offboarding"]["pending_requests"]
    assert len(requests) == 1
    assert requests[0]["user_id"] == target["user"]["id"]
    assert set(requests[0]) == {"user_id", "state", "requested_at", "erasure_after"}
    serialized = json.dumps(report, default=str)
    assert "delete-me@example.test" not in serialized
    assert "recipient_hash" not in serialized
    assert "encrypted_payload" not in serialized
