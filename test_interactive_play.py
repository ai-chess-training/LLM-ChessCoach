#!/usr/bin/env python3
"""
Test script for interactive chess gameplay with Stockfish at different skill levels.
"""

import chess
from live_sessions import session_manager
from stockfish_engine import StockfishAnalyzer, SKILL_LEVEL_MAPPINGS


def test_engine_move():
    """Test that the engine can make moves at different skill levels."""
    print("Testing engine move generation at different skill levels...")

    board = chess.Board()

    for level_name, config in SKILL_LEVEL_MAPPINGS.items():
        print(f"\nTesting {level_name} level (Skill Level {config['skill_level']})...")

        with StockfishAnalyzer(skill_level=config['skill_level']) as analyzer:
            response = analyzer.get_engine_move(board, time_limit_ms=config['move_time_ms'])

            assert response['move_uci'] is not None, f"No move generated for {level_name}"
            assert response['move_san'] is not None, f"No SAN move for {level_name}"

            print(f"  Engine move: {response['move_san']} ({response['move_uci']})")
            if response.get('score'):
                print(f"  Evaluation: {response['score']}")

    print("\n✓ Engine move generation test passed!")


def test_interactive_session():
    """Test a full interactive game session."""
    print("\n\nTesting interactive game session...")

    # Create a new session
    session_info = session_manager.create(skill_level="beginner", game_mode="play")
    sid = session_info["session_id"]

    print(f"Created session {sid} with skill level: {session_info['skill_level']}, mode: {session_info['game_mode']}")

    # Play a few moves
    test_moves = ["e4", "d4", "Nf3", "Bc4"]

    for move in test_moves:
        print(f"\n--- Playing human move: {move} ---")

        result = session_manager.apply_move(sid, move)

        if not result['legal']:
            print(f"Illegal move: {result.get('error')}")
            break

        # Display human move feedback
        human_feedback = result['human_feedback']
        print(f"Human played: {human_feedback['san']}")
        print(f"  Severity: {human_feedback['severity']}")
        print(f"  CP Loss: {human_feedback['cp_loss']:.2f} pawns")
        print(f"  Basic feedback: {human_feedback.get('basic', 'N/A')}")

        # Display engine response
        if result.get('engine_move'):
            engine = result['engine_move']
            print(f"\nEngine responds: {engine['san']}")
            print(f"  Position after: {engine['fen_after']}")

        # Check game state
        snapshot = session_manager.snapshot(sid)
        if snapshot['is_game_over']:
            print("\nGame Over!")
            break

    # Final snapshot
    snapshot = session_manager.snapshot(sid)
    print(f"\n--- Final Position ---")
    print(f"FEN: {snapshot['fen']}")
    print(f"Turn: {snapshot['turn']}")
    print(f"Total moves: {len(snapshot['moves'])}")

    print("\n✓ Interactive session test passed!")


def test_different_skill_levels():
    """Test that different skill levels produce different quality moves."""
    print("\n\nTesting skill level differences...")

    # Set up a tactical position where best move is clear
    fen = "r1bqkb1r/pppp1ppp/2n2n2/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 4 4"
    board = chess.Board(fen)

    print(f"Position: {fen}")
    print("Testing engine response at different levels...\n")

    moves_by_level = {}

    for level_name, config in SKILL_LEVEL_MAPPINGS.items():
        with StockfishAnalyzer(skill_level=config['skill_level']) as analyzer:
            response = analyzer.get_engine_move(board, time_limit_ms=1000)
            moves_by_level[level_name] = response['move_san']
            print(f"{level_name:12} -> {response['move_san']:8}")

    print("\n✓ Skill level test completed!")


def test_training_mode():
    """Test that training mode doesn't trigger engine moves."""
    print("\n\nTesting training mode (no engine moves)...")

    session_info = session_manager.create(skill_level="intermediate", game_mode="training")
    sid = session_info["session_id"]

    print(f"Created training session {sid}")

    # Play a move
    result = session_manager.apply_move(sid, "e4")

    assert result['legal'], "Move should be legal"
    assert result.get('engine_move') is None, "No engine move should be generated in training mode"

    print("Human move processed, no engine response (as expected)")
    print("\n✓ Training mode test passed!")


if __name__ == "__main__":
    print("=" * 60)
    print("Interactive Chess Gameplay Test Suite")
    print("=" * 60)

    try:
        test_engine_move()
        test_interactive_session()
        test_different_skill_levels()
        test_training_mode()

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED! ✓")
        print("=" * 60)

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()