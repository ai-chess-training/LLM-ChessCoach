"""Tests for the undo move API and session manager undo logic."""

import chess
import pytest

from live_sessions import SessionManager


@pytest.fixture()
def manager():
    return SessionManager()


def test_undo_on_empty_session_raises(manager):
    result = manager.create(skill_level="intermediate", game_mode="training")
    with pytest.raises(ValueError, match="No moves to undo"):
        manager.undo_last_move(result["session_id"])


@pytest.mark.asyncio
async def test_undo_restores_board_state(manager):
    """Undo in training mode pops a single human move."""
    result = manager.create(skill_level="intermediate", game_mode="training")
    sid = result["session_id"]
    sess = manager.get(sid)
    board = sess["board"]
    initial_fen = board.fen()

    # Manually push a move (bypass engine analysis which needs Stockfish)
    board.push_san("e4")
    sess["moves"].append({"san": "e4", "uci": "e2e4", "side": "white", "move_no": 1})
    manager.save(sess)

    undo_result = manager.undo_last_move(sid)
    assert undo_result["undone_count"] == 1
    assert undo_result["fen"] == initial_fen
    assert undo_result["turn"] == "white"
    assert len(undo_result["moves"]) == 0


def test_undo_pops_engine_and_human_in_play_mode(manager):
    """In play mode, undo pops both the engine move and the human move."""
    result = manager.create(skill_level="intermediate", game_mode="play")
    sid = result["session_id"]
    sess = manager.get(sid)
    board = sess["board"]
    initial_fen = board.fen()

    # Simulate human move + engine response
    board.push_san("e4")
    sess["moves"].append({"san": "e4", "uci": "e2e4", "side": "white", "move_no": 1})
    board.push_san("e5")
    sess["moves"].append({"san": "e5", "uci": "e7e5", "side": "black", "move_no": 1, "is_engine_move": True})
    manager.save(sess)

    undo_result = manager.undo_last_move(sid)
    assert undo_result["undone_count"] == 2
    assert undo_result["fen"] == initial_fen
    assert len(undo_result["moves"]) == 0


def test_multiple_undos(manager):
    """Multiple sequential undos work correctly."""
    result = manager.create(skill_level="intermediate", game_mode="training")
    sid = result["session_id"]
    sess = manager.get(sid)
    board = sess["board"]

    board.push_san("e4")
    sess["moves"].append({"san": "e4", "uci": "e2e4", "side": "white", "move_no": 1})
    board.push_san("e5")
    sess["moves"].append({"san": "e5", "uci": "e7e5", "side": "black", "move_no": 2})
    manager.save(sess)

    # First undo
    r1 = manager.undo_last_move(sid)
    assert r1["undone_count"] == 1
    assert len(r1["moves"]) == 1

    # Second undo
    r2 = manager.undo_last_move(sid)
    assert r2["undone_count"] == 1
    assert len(r2["moves"]) == 0
    assert r2["fen"] == chess.Board().fen()
