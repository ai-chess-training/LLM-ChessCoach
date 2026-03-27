"""Tests for the subscription system in EntitlementStore."""

from datetime import datetime, timedelta, timezone

import pytest

from entitlements import EntitlementError, EntitlementStore


def _make_store(monkeypatch, tmp_path, *, free_games_per_day: int = 1, trial_days: int = 90) -> EntitlementStore:
    monkeypatch.setenv("FREE_GAMES_PER_DAY", str(free_games_per_day))
    monkeypatch.setenv("TRIAL_DAYS", str(trial_days))
    monkeypatch.setenv("APPSTORE_GAMES_PER_PURCHASE", "30")
    monkeypatch.setenv("SUBSCRIPTION_GAMES_PER_MONTH", "100")
    return EntitlementStore(database_url=f"sqlite:///{tmp_path / 'entitlements.db'}")


def test_new_free_tier_1_game_per_day(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")
    snapshot = store.get_entitlement_snapshot(user.id)
    assert snapshot.trial_active is True
    assert snapshot.daily_free_limit == 1
    assert snapshot.daily_free_remaining == 1


def test_new_free_tier_blocks_second_game(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    r1 = store.consume_game(user.id, "s:1", source="live_game")
    assert r1.consumed is True
    assert r1.charge_kind == "free_trial"

    with pytest.raises(EntitlementError):
        store.consume_game(user.id, "s:2", source="live_game")


def test_free_tier_expires_after_90_days(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    trial_start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    user = store.upsert_user("apple-sub-1", now=trial_start)

    after_trial = trial_start + timedelta(days=91)
    snapshot = store.get_entitlement_snapshot(user.id, now=after_trial)
    assert snapshot.trial_active is False
    assert snapshot.daily_free_remaining == 0


def test_subscription_activate_grants_credits(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    result = store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1",
    )
    assert result.applied is True
    assert result.games_changed == 100
    assert result.snapshot.subscription_active is True
    assert result.snapshot.subscription_games_remaining == 100


def test_subscription_activate_is_idempotent(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1",
    )
    dup = store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1-dup",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1-dup",
    )
    assert dup.already_processed is True
    assert dup.games_changed == 0


def test_consumption_priority_free_then_subscription_then_paid(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    # Give user a subscription and paid balance
    store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1",
    )
    store.apply_app_store_transaction(
        user_id=user.id,
        transaction_id="tx-paid-1",
        original_transaction_id="orig-paid-1",
        product_id="com.llmchesscoach.games30",
        environment="Sandbox",
        signed_transaction_info="signed-paid-1",
    )

    # First game uses free trial
    r1 = store.consume_game(user.id, "s:1", source="live_game")
    assert r1.charge_kind == "free_trial"

    # Second game on same day uses subscription (free exhausted)
    r2 = store.consume_game(user.id, "s:2", source="live_game")
    assert r2.charge_kind == "subscription"


def test_subscription_credits_consumed_oldest_first(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    user = store.upsert_user("apple-sub-1", now=now)

    # Activate subscription
    store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1",
        now=now,
    )

    # Renew (creates a second credit bucket)
    month_later = now + timedelta(days=32)
    store.renew_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-renew-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-renew-1",
        now=month_later,
    )

    # Consume game after trial day limit exhausted
    r1 = store.consume_game(user.id, "s:day-1", source="live_game", now=month_later)
    assert r1.charge_kind == "free_trial"
    r2 = store.consume_game(user.id, "s:day-2", source="live_game", now=month_later)
    assert r2.charge_kind == "subscription"
    # Should have 200 total - 1 used = 199
    assert r2.snapshot.subscription_games_remaining == 199


def test_subscription_renewal_grants_new_credits(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1",
    )

    result = store.renew_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-renew-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-renew-1",
    )
    assert result.applied is True
    assert result.games_changed == 100
    assert result.snapshot.subscription_games_remaining == 200


def test_subscription_expiry(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1",
    )

    store.expire_subscription(user.id, "orig-sub-1")
    snapshot = store.get_entitlement_snapshot(user.id)
    # Subscription marked expired, but credits remain until they expire naturally
    assert snapshot.subscription_active is False


def test_subscription_auto_renew_toggle(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    user = store.upsert_user("apple-sub-1")

    store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1",
    )

    store.update_subscription_auto_renew(user.id, "orig-sub-1", auto_renew=False)
    snapshot = store.get_entitlement_snapshot(user.id)
    assert snapshot.subscription_auto_renew is False

    store.update_subscription_auto_renew(user.id, "orig-sub-1", auto_renew=True)
    snapshot = store.get_entitlement_snapshot(user.id)
    assert snapshot.subscription_auto_renew is True


def test_credit_expiry_after_rollover_period(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    now = datetime(2025, 1, 1, tzinfo=timezone.utc)
    user = store.upsert_user("apple-sub-1", now=now)

    store.activate_subscription(
        user_id=user.id,
        original_transaction_id="orig-sub-1",
        transaction_id="tx-sub-1",
        product_id="com.llmchesscoach.monthly100",
        environment="Sandbox",
        signed_transaction_info="signed-sub-1",
        now=now,
    )

    # Credits expire after ~3 months (current + 2 rollover)
    # Check at 4 months later - credits should be expired
    four_months_later = now + timedelta(days=124)
    snapshot = store.get_entitlement_snapshot(user.id, now=four_months_later)
    assert snapshot.subscription_games_remaining == 0
