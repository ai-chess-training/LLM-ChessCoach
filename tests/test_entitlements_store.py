from datetime import datetime, timedelta, timezone

import pytest

from entitlements import EntitlementError, EntitlementStore


def _make_store(monkeypatch, tmp_path, *, free_games_per_day: int = 5, trial_days: int = 14) -> EntitlementStore:
    monkeypatch.setenv("FREE_GAMES_PER_DAY", str(free_games_per_day))
    monkeypatch.setenv("TRIAL_DAYS", str(trial_days))
    monkeypatch.setenv("APPSTORE_GAMES_PER_PURCHASE", "30")
    return EntitlementStore(database_url=f"sqlite:///{tmp_path / 'entitlements.db'}")


def test_upsert_user_creates_trial_snapshot(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)

    user = store.upsert_user("apple-sub-1", apple_email="one@example.com")
    snapshot = store.get_entitlement_snapshot(user.id)

    assert snapshot.trial_active is True
    assert snapshot.daily_free_limit == 5
    assert snapshot.daily_free_remaining == 5
    assert snapshot.paid_games_balance == 0


def test_consume_game_is_idempotent_for_same_event_key(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    first = store.consume_game(user.id, "session:1", source="live_game")
    second = store.consume_game(user.id, "session:1", source="live_game")

    assert first.consumed is True
    assert second.consumed is False
    assert second.snapshot.daily_free_remaining == 4


def test_consume_game_uses_paid_balance_after_trial_expires(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    trial_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    after_trial = trial_start + timedelta(days=15)

    user = store.upsert_user("apple-sub-1", now=trial_start)
    store.apply_app_store_transaction(
        user_id=user.id,
        transaction_id="tx-1",
        original_transaction_id="orig-1",
        product_id="com.llmchesscoach.games30",
        environment="Sandbox",
        signed_transaction_info="signed-1",
        now=trial_start,
    )

    result = store.consume_game(user.id, "session:paid", source="live_game", now=after_trial)

    assert result.charge_kind == "paid"
    assert result.snapshot.trial_active is False
    assert result.snapshot.paid_games_balance == 29


def test_assert_can_play_raises_when_trial_is_over_and_no_paid_balance(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    trial_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    after_trial = trial_start + timedelta(days=15)

    user = store.upsert_user("apple-sub-1", now=trial_start)

    with pytest.raises(EntitlementError) as exc:
        store.assert_can_play(user.id, now=after_trial)

    assert exc.value.snapshot.can_play is False
    assert exc.value.snapshot.daily_free_remaining == 0


def test_app_store_transactions_are_idempotent_and_refunds_reverse_balance(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    first = store.apply_app_store_transaction(
        user_id=user.id,
        transaction_id="tx-1",
        original_transaction_id="orig-1",
        product_id="com.llmchesscoach.games30",
        environment="Sandbox",
        signed_transaction_info="signed-1",
    )
    duplicate = store.apply_app_store_transaction(
        user_id=user.id,
        transaction_id="tx-1",
        original_transaction_id="orig-1",
        product_id="com.llmchesscoach.games30",
        environment="Sandbox",
        signed_transaction_info="signed-1",
    )
    refund = store.apply_app_store_transaction(
        user_id=user.id,
        transaction_id="tx-1",
        original_transaction_id="orig-1",
        product_id="com.llmchesscoach.games30",
        environment="Sandbox",
        signed_transaction_info="signed-1-refund",
        revoked=True,
    )

    assert first.games_changed == 30
    assert duplicate.already_processed is True
    assert duplicate.games_changed == 0
    assert refund.revoked is True
    assert refund.games_changed == -30
    assert refund.snapshot.paid_games_balance == 0
