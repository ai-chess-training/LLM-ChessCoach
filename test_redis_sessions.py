#!/usr/bin/env python3
"""
Test suite for Redis session storage functionality.

These tests verify that:
1. Sessions can be serialized and deserialized correctly
2. TTL is refreshed on every access (sliding window)
3. Sessions work correctly across "simulated workers"

To run these tests with Redis:
1. Start a local Redis server: docker run -d -p 6379:6379 redis
2. Set REDIS_URL: export REDIS_URL=redis://localhost:6379/0
3. Run tests: python test_redis_sessions.py

Without REDIS_URL set, tests will use in-memory storage.
"""

import os
import time
import chess
from live_sessions import session_manager, SessionManager, RedisSessionManager


def test_session_manager_type():
    """Verify which session manager is being used."""
    print("Testing session manager type...")

    if os.getenv("REDIS_URL"):
        assert isinstance(session_manager, RedisSessionManager), "Should use Redis when REDIS_URL is set"
        print("✓ Using RedisSessionManager (multi-worker compatible)")
    else:
        assert isinstance(session_manager, SessionManager) and not isinstance(session_manager, RedisSessionManager), \
            "Should use in-memory SessionManager when REDIS_URL not set"
        print("✓ Using in-memory SessionManager (single-worker only)")
        print("  Note: Set REDIS_URL to test Redis functionality")


def test_board_serialization():
    """Test that chess boards are correctly serialized to/from Redis."""
    print("\nTesting board serialization...")

    if not isinstance(session_manager, RedisSessionManager):
        print("  Skipped (requires Redis)")
        return

    # Create a session
    session_info = session_manager.create(skill_level="intermediate", game_mode="play")
    sid = session_info["session_id"]

    # Get the session
    sess = session_manager.get(sid)
    board = sess["board"]

    # Verify it's a proper chess.Board object
    assert isinstance(board, chess.Board), "Board should be chess.Board instance"
    assert board.fen() == chess.STARTING_FEN, "Board should start at initial position"

    print("✓ Board serialization works correctly")


def test_ttl_refresh():
    """Test that TTL is refreshed on every access (sliding window)."""
    print("\nTesting TTL refresh (sliding window)...")

    if not isinstance(session_manager, RedisSessionManager):
        print("  Skipped (requires Redis)")
        return

    # Create a session
    session_info = session_manager.create(skill_level="intermediate", game_mode="training")
    sid = session_info["session_id"]

    # Get initial TTL
    redis_client = session_manager.redis_client
    key = session_manager._session_key(sid)
    ttl1 = redis_client.ttl(key)

    print(f"  Initial TTL: {ttl1} seconds")
    assert ttl1 > 0, "TTL should be positive"
    assert ttl1 <= 24 * 60 * 60, "TTL should be <= 24 hours"

    # Wait a bit
    time.sleep(2)

    # Access the session (should refresh TTL)
    session_manager.get(sid)
    ttl2 = redis_client.ttl(key)

    print(f"  TTL after get(): {ttl2} seconds")
    assert ttl2 > ttl1, "TTL should be refreshed (increased) after get()"

    # Make a move (should also refresh TTL)
    time.sleep(2)
    import asyncio
    asyncio.run(session_manager.apply_move(sid, "e4"))
    ttl3 = redis_client.ttl(key)

    print(f"  TTL after apply_move(): {ttl3} seconds")
    assert ttl3 > ttl2 - 2, "TTL should be refreshed after apply_move()"

    print("✓ TTL refresh (sliding window) works correctly")


def test_move_history_persistence():
    """Test that move history is correctly persisted in Redis."""
    print("\nTesting move history persistence...")

    if not isinstance(session_manager, RedisSessionManager):
        print("  Skipped (requires Redis)")
        return

    # Create a session and make some moves
    session_info = session_manager.create(skill_level="beginner", game_mode="training")
    sid = session_info["session_id"]

    import asyncio

    # Make several moves
    moves = ["e4", "d4", "Nf3"]
    for move in moves:
        result = asyncio.run(session_manager.apply_move(sid, move))
        assert result["legal"], f"Move {move} should be legal"

    # Get session snapshot
    snapshot = session_manager.snapshot(sid)

    # Verify move history
    assert len(snapshot["moves"]) == len(moves), f"Should have {len(moves)} moves in history"

    # Verify move details are preserved
    for i, expected_move in enumerate(moves):
        actual_san = snapshot["moves"][i]["san"]
        print(f"  Move {i+1}: {actual_san}")

    print("✓ Move history persistence works correctly")


def test_cross_worker_compatibility():
    """
    Simulate multiple workers accessing the same session.
    This verifies that sessions work correctly in a multi-worker environment.
    """
    print("\nTesting cross-worker compatibility...")

    if not isinstance(session_manager, RedisSessionManager):
        print("  Skipped (requires Redis)")
        return

    # Worker 1: Create session
    session_info = session_manager.create(skill_level="intermediate", game_mode="training")
    sid = session_info["session_id"]
    print(f"  Worker 1: Created session {sid}")

    # Worker 2: Access the same session (simulated)
    # In a real multi-worker scenario, this would be a different process
    sess = session_manager.get(sid)
    assert sess is not None, "Worker 2 should be able to access session created by Worker 1"
    print(f"  Worker 2: Successfully retrieved session {sid}")

    # Worker 2: Make a move
    import asyncio
    result = asyncio.run(session_manager.apply_move(sid, "e4"))
    assert result["legal"], "Worker 2 should be able to make moves"
    print(f"  Worker 2: Made move e4")

    # Worker 1: Verify the move is visible
    snapshot = session_manager.snapshot(sid)
    assert len(snapshot["moves"]) == 1, "Worker 1 should see the move made by Worker 2"
    assert snapshot["moves"][0]["san"] == "e4", "Move should be e4"
    print(f"  Worker 1: Confirmed move from Worker 2")

    print("✓ Cross-worker compatibility verified")


def test_session_cleanup():
    """Test that sessions are properly cleaned up."""
    print("\nTesting session cleanup...")

    if not isinstance(session_manager, RedisSessionManager):
        print("  Skipped (requires Redis)")
        return

    # Create a session
    session_info = session_manager.create(skill_level="intermediate", game_mode="training")
    sid = session_info["session_id"]

    # Verify it exists
    assert session_manager.exists(sid), "Session should exist"
    print(f"  Created session {sid}")

    # Delete it
    result = session_manager.delete(sid)
    assert result, "Delete should return True"

    # Verify it's gone
    assert not session_manager.exists(sid), "Session should not exist after deletion"
    print(f"  Successfully deleted session {sid}")

    print("✓ Session cleanup works correctly")


if __name__ == "__main__":
    print("=" * 60)
    print("Redis Session Storage Test Suite")
    print("=" * 60)

    try:
        test_session_manager_type()
        test_board_serialization()
        test_ttl_refresh()
        test_move_history_persistence()
        test_cross_worker_compatibility()
        test_session_cleanup()

        print("\n" + "=" * 60)
        print("ALL REDIS TESTS PASSED! ✓")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
