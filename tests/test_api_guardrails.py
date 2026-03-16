from fastapi.testclient import TestClient

from apple_auth import build_test_identity_token
from token_utils import encode_hs256_jwt


def _auth_headers(client: TestClient, apple_sub: str):
    token = build_test_identity_token(
        apple_sub=apple_sub,
        bundle_id="com.llmchesscoach.test",
        secret="apple-test-secret",
        nonce="guardrail-nonce",
    )
    response = client.post("/v1/auth/apple", json={"identity_token": token, "nonce": "guardrail-nonce"})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _stub_apply_move(module):
    async def fake_apply_move(session_id: str, move: str):
        return {
            "legal": True,
            "human_feedback": {
                "move_no": 1,
                "side": "white",
                "san": move,
                "uci": "e2e4",
                "fen_before": "start",
                "fen_after": "after",
                "cp_before": 0,
                "cp_after": 0,
                "cp_loss": 0.0,
                "severity": "best",
                "best_move_san": move,
                "multipv": [],
                "basic": "Good move",
            },
            "engine_move": None,
        }

    module.session_manager.apply_move = fake_apply_move


def test_missing_auth_is_rejected(app_client_factory):
    client, _module = app_client_factory(api_key="dev-key", db_name="missing_auth.db")

    response = client.get("/v1/entitlements")

    assert response.status_code == 401


def test_development_api_key_bypass_can_use_v1_endpoints(app_client_factory):
    client, module = app_client_factory(api_key="dev-key", db_name="dev_api_key.db")
    _stub_apply_move(module)
    headers = {"Authorization": "Bearer dev-key"}

    created = client.post("/v1/sessions", params={"skill_level": "intermediate"}, headers=headers)
    assert created.status_code == 200, created.text
    session_id = created.json()["session_id"]

    first = client.post(f"/v1/sessions/{session_id}/move", params={"move": "e4"}, headers=headers)
    second = client.post(f"/v1/sessions/{session_id}/move", params={"move": "e4"}, headers=headers)
    entitlements = client.get("/v1/entitlements", headers=headers)

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert entitlements.status_code == 200, entitlements.text
    assert entitlements.json()["daily_free_remaining"] == 4


def test_purchase_endpoint_rejects_unexpected_product(app_client_factory):
    client, _module = app_client_factory(db_name="wrong_product.db")
    headers = _auth_headers(client, "wrong-product-user")

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

    response = client.post("/v1/purchases/app-store", json={"signed_transaction_info": signed_transaction}, headers=headers)

    assert response.status_code == 400


def test_unknown_webhook_transaction_is_ignored(app_client_factory):
    client, _module = app_client_factory(db_name="unknown_webhook.db")

    signed_transaction = encode_hs256_jwt(
        {
            "transactionId": "unknown-tx",
            "originalTransactionId": "orig-1",
            "productId": "com.llmchesscoach.games30",
            "bundleId": "com.llmchesscoach.test",
            "environment": "Sandbox",
            "revocationDate": 1710000000,
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

    response = client.post("/v1/webhooks/app-store", json={"signedPayload": signed_notification})

    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
