import importlib

import pytest
from fastapi.testclient import TestClient

from apple_auth import build_test_identity_token
from token_utils import encode_hs256_jwt


@pytest.fixture()
def app_client(monkeypatch, tmp_path):
    db_path = tmp_path / "auth_billing.db"
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("BACKEND_AUTH_SECRET", "backend-test-secret")
    monkeypatch.setenv("APPLE_BUNDLE_ID", "com.llmchesscoach.test")
    monkeypatch.setenv("APPLE_TEST_IDENTITY_SECRET", "apple-test-secret")
    monkeypatch.setenv("APPSTORE_TEST_SHARED_SECRET", "app-store-test-secret")
    monkeypatch.setenv("APPSTORE_PRODUCT_ID_30_GAMES", "com.llmchesscoach.games30")
    monkeypatch.setenv("FREE_GAMES_PER_DAY", "5")
    monkeypatch.setenv("TRIAL_DAYS", "14")
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)

    import live_sessions
    import api_server

    importlib.reload(live_sessions)
    reloaded_api_server = importlib.reload(api_server)

    with TestClient(reloaded_api_server.app) as client:
        yield client, reloaded_api_server


def _auth_headers(client: TestClient, apple_sub: str, nonce: str = "test-nonce", email: str = "user@example.com"):
    token = build_test_identity_token(
        apple_sub=apple_sub,
        bundle_id="com.llmchesscoach.test",
        secret="apple-test-secret",
        nonce=nonce,
        email=email,
    )
    response = client.post("/v1/auth/apple", json={"identity_token": token, "nonce": nonce})
    assert response.status_code == 200, response.text
    access_token = response.json()["access_token"]
    return {"Authorization": f"Bearer {access_token}"}


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


def _stub_batch_analysis(module):
    async def fake_analyze_pgn_to_feedback(pgn: str, level: str = "intermediate"):
        return {
            "moves": [
                {
                    "move_no": 1,
                    "side": "white",
                    "san": "e4",
                    "uci": "e2e4",
                    "fen_before": "start",
                    "fen_after": "after",
                    "cp_before": 0,
                    "cp_after": 0,
                    "cp_loss": 0.0,
                    "severity": "best",
                    "best_move_san": "e4",
                    "multipv": [],
                    "basic": "Good move",
                }
            ]
        }

    module.analyze_pgn_to_feedback = fake_analyze_pgn_to_feedback


def test_apple_auth_rejects_nonce_mismatch(app_client):
    client, _module = app_client
    token = build_test_identity_token(
        apple_sub="apple-user-1",
        bundle_id="com.llmchesscoach.test",
        secret="apple-test-secret",
        nonce="expected-nonce",
    )
    response = client.post("/v1/auth/apple", json={"identity_token": token, "nonce": "wrong-nonce"})
    assert response.status_code == 401


def test_sessions_are_bound_to_authenticated_user(app_client):
    client, module = app_client
    headers_one = _auth_headers(client, "apple-user-1")
    headers_two = _auth_headers(client, "apple-user-2", email="other@example.com")

    response = client.post("/v1/sessions", params={"skill_level": "intermediate"}, headers=headers_one)
    assert response.status_code == 200, response.text
    session_id = response.json()["session_id"]

    get_other = client.get(f"/v1/sessions/{session_id}", headers=headers_two)
    assert get_other.status_code == 404

    _stub_apply_move(module)
    move_other = client.post(f"/v1/sessions/{session_id}/move", params={"move": "e4"}, headers=headers_two)
    assert move_other.status_code == 404


def test_trial_limit_blocks_the_sixth_game(app_client):
    client, module = app_client
    _stub_apply_move(module)
    headers = _auth_headers(client, "trial-user")

    for _ in range(5):
        created = client.post("/v1/sessions", params={"skill_level": "intermediate"}, headers=headers)
        assert created.status_code == 200, created.text
        session_id = created.json()["session_id"]
        moved = client.post(f"/v1/sessions/{session_id}/move", params={"move": "e4"}, headers=headers)
        assert moved.status_code == 200, moved.text

    blocked = client.post("/v1/sessions", params={"skill_level": "intermediate"}, headers=headers)
    assert blocked.status_code == 402
    assert blocked.json()["detail"]["daily_free_remaining"] == 0


def test_batch_analysis_uses_idempotency_and_only_charges_after_validation(app_client):
    client, module = app_client
    _stub_batch_analysis(module)
    headers = _auth_headers(client, "analysis-user")

    invalid = client.post("/v1/runs", data={"pgn": "not a pgn", "level": "intermediate"}, headers=headers)
    assert invalid.status_code == 400

    before = client.get("/v1/entitlements", headers=headers)
    assert before.status_code == 200
    assert before.json()["daily_free_remaining"] == 5

    valid_pgn = "[Event \"Test\"]\n[White \"W\"]\n[Black \"B\"]\n\n1. e4 e5 1-0\n"
    first = client.post(
        "/v1/runs",
        data={"pgn": valid_pgn, "level": "intermediate"},
        headers={**headers, "Idempotency-Key": "analysis-1"},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/v1/runs",
        data={"pgn": valid_pgn, "level": "intermediate"},
        headers={**headers, "Idempotency-Key": "analysis-1"},
    )
    assert second.status_code == 200, second.text

    after = client.get("/v1/entitlements", headers=headers)
    assert after.status_code == 200
    assert after.json()["daily_free_remaining"] == 4


def test_purchase_endpoint_is_idempotent_and_webhook_can_revoke(app_client):
    client, _module = app_client
    headers = _auth_headers(client, "billing-user")

    signed_transaction = encode_hs256_jwt(
        {
            "transactionId": "tx-123",
            "originalTransactionId": "orig-123",
            "productId": "com.llmchesscoach.games30",
            "bundleId": "com.llmchesscoach.test",
            "environment": "Sandbox",
        },
        "app-store-test-secret",
    )

    purchase = client.post("/v1/purchases/app-store", json={"signed_transaction_info": signed_transaction}, headers=headers)
    assert purchase.status_code == 200, purchase.text
    assert purchase.json()["games_changed"] == 30
    assert purchase.json()["entitlement"]["paid_games_balance"] == 30

    duplicate = client.post("/v1/purchases/app-store", json={"signed_transaction_info": signed_transaction}, headers=headers)
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["already_processed"] is True
    assert duplicate.json()["games_changed"] == 0

    refunded_transaction = encode_hs256_jwt(
        {
            "transactionId": "tx-123",
            "originalTransactionId": "orig-123",
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
            "subtype": None,
            "data": {"signedTransactionInfo": refunded_transaction},
        },
        "app-store-test-secret",
    )

    webhook = client.post("/v1/webhooks/app-store", json={"signedPayload": signed_notification})
    assert webhook.status_code == 200, webhook.text
    assert webhook.json()["games_changed"] == -30

    entitlements = client.get("/v1/entitlements", headers=headers)
    assert entitlements.status_code == 200
    assert entitlements.json()["paid_games_balance"] == 0
