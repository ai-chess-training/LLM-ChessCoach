import hashlib
from datetime import datetime, timedelta, timezone

import pytest

from apple_auth import APPLE_ISSUER, AppleIdentityError, build_test_identity_token, verify_apple_identity_token
from app_store import AppStoreVerificationError, verify_notification, verify_signed_transaction
from auth_service import AuthContext, authenticate_bearer_token, authenticate_development_api_key, issue_backend_token
from entitlements import EntitlementStore
from token_utils import encode_hs256_jwt


def test_verify_apple_identity_token_accepts_hashed_nonce(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("APPLE_BUNDLE_ID", "com.llmchesscoach.test")
    monkeypatch.setenv("APPLE_TEST_IDENTITY_SECRET", "apple-test-secret")

    raw_nonce = "nonce-raw"
    token = build_test_identity_token(
        apple_sub="apple-user-1",
        bundle_id="com.llmchesscoach.test",
        secret="apple-test-secret",
        nonce=hashlib.sha256(raw_nonce.encode("utf-8")).hexdigest(),
    )

    claims = verify_apple_identity_token(token, nonce=raw_nonce)

    assert claims["sub"] == "apple-user-1"


def test_verify_apple_identity_token_rejects_missing_subject(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("APPLE_BUNDLE_ID", "com.llmchesscoach.test")
    monkeypatch.setenv("APPLE_TEST_IDENTITY_SECRET", "apple-test-secret")

    token = encode_hs256_jwt(
        {
            "iss": APPLE_ISSUER,
            "aud": "com.llmchesscoach.test",
            "iat": int(datetime.now(tz=timezone.utc).timestamp()),
            "exp": int((datetime.now(tz=timezone.utc) + timedelta(minutes=10)).timestamp()),
        },
        "apple-test-secret",
    )

    with pytest.raises(AppleIdentityError, match="missing subject"):
        verify_apple_identity_token(token)


def test_backend_token_round_trip(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("BACKEND_AUTH_SECRET", "backend-secret")
    store = EntitlementStore(database_url=f"sqlite:///{tmp_path / 'auth.db'}")
    user = store.upsert_user("apple-user-1", apple_email="one@example.com")

    token = issue_backend_token(user)
    context = authenticate_bearer_token(token, store)

    assert isinstance(context, AuthContext)
    assert context.user_id == user.id
    assert context.apple_sub == "apple-user-1"


def test_development_api_key_fallback_creates_dev_user(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("API_KEY", "dev-api-key")
    store = EntitlementStore(database_url=f"sqlite:///{tmp_path / 'auth.db'}")

    context = authenticate_development_api_key("dev-api-key", store)

    assert context.is_development_override is True
    assert context.apple_sub == "dev-api-key-user"


def test_verify_signed_transaction_rejects_wrong_product(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("APPLE_BUNDLE_ID", "com.llmchesscoach.test")
    monkeypatch.setenv("APPSTORE_PRODUCT_ID_30_GAMES", "com.llmchesscoach.games30")
    monkeypatch.setenv("APPSTORE_TEST_SHARED_SECRET", "app-store-test-secret")

    signed_transaction = encode_hs256_jwt(
        {
            "transactionId": "tx-1",
            "originalTransactionId": "orig-1",
            "productId": "wrong.product",
            "bundleId": "com.llmchesscoach.test",
            "environment": "Sandbox",
        },
        "app-store-test-secret",
    )

    with pytest.raises(AppStoreVerificationError, match="Unexpected App Store productId"):
        verify_signed_transaction(signed_transaction)


def test_verify_notification_decodes_nested_signed_transaction(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("APPLE_BUNDLE_ID", "com.llmchesscoach.test")
    monkeypatch.setenv("APPSTORE_PRODUCT_ID_30_GAMES", "com.llmchesscoach.games30")
    monkeypatch.setenv("APPSTORE_TEST_SHARED_SECRET", "app-store-test-secret")

    signed_transaction = encode_hs256_jwt(
        {
            "transactionId": "tx-1",
            "originalTransactionId": "orig-1",
            "productId": "com.llmchesscoach.games30",
            "bundleId": "com.llmchesscoach.test",
            "environment": "Sandbox",
        },
        "app-store-test-secret",
    )
    signed_notification = encode_hs256_jwt(
        {
            "notificationType": "REFUND",
            "data": {"signedTransactionInfo": signed_transaction},
        },
        "app-store-test-secret",
    )

    notification = verify_notification(signed_notification)

    assert notification.notification_type == "REFUND"
    assert notification.transaction is not None
    assert notification.transaction.transaction_id == "tx-1"
