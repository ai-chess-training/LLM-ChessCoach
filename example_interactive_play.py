#!/usr/bin/env python3
"""
Example: Interactive Chess Game with Coaching

This demonstrates how to play an interactive chess game against Stockfish
at different skill levels while receiving real-time coaching feedback.
"""

import requests
import json


def create_game_session(skill_level="beginner"):
    """Create a new interactive game session."""
    response = requests.post(
        "http://localhost:8000/v1/sessions",
        params={"skill_level": skill_level, "game_mode": "play"}
    )
    return response.json()


def make_move(session_id, move):
    """Make a move and get feedback plus engine response."""
    response = requests.post(
        f"http://localhost:8000/v1/sessions/{session_id}/move",
        params={"move": move}
    )
    return response.json()


def get_session_status(session_id):
    """Get current game status."""
    response = requests.get(f"http://localhost:8000/v1/sessions/{session_id}")
    return response.json()


def print_feedback(result):
    """Pretty print move feedback."""
    if not result.get("legal"):
        print(f"❌ Illegal move: {result.get('error')}")
        return False

    # Human move feedback
    human = result.get("human_feedback", {})
    print(f"\n👤 You played: {human.get('san')}")
    print(f"   Evaluation: {human.get('severity', 'N/A')}")

    cp_loss = human.get('cp_loss', 0)
    if cp_loss > 0.5:
        print(f"   ⚠️  Lost {cp_loss:.2f} pawns")
        if human.get('best_move_san'):
            print(f"   💡 Better was: {human.get('best_move_san')}")

    if human.get('basic'):
        print(f"   Coach says: {human.get('basic')}")

    # Engine move
    engine = result.get("engine_move")
    if engine:
        print(f"\n🤖 Stockfish plays: {engine.get('san')}")

    return True


def main():
    print("=" * 60)
    print("INTERACTIVE CHESS GAME WITH COACHING")
    print("=" * 60)

    # Select difficulty
    print("\nSelect difficulty level:")
    print("1. Beginner (Skill Level 3)")
    print("2. Intermediate (Skill Level 8)")
    print("3. Advanced (Skill Level 13)")
    print("4. Expert (Skill Level 18)")

    choice = input("\nEnter choice (1-4): ").strip()
    levels = ["beginner", "intermediate", "advanced", "expert"]
    skill_level = levels[int(choice) - 1] if choice in "1234" else "beginner"

    print(f"\nStarting game at {skill_level} level...")

    # Create session
    session = create_game_session(skill_level)
    session_id = session["session_id"]

    print(f"Game started! Session ID: {session_id}")
    print("\nEnter moves in standard notation (e.g., e4, Nf3, O-O)")
    print("Type 'quit' to end the game\n")

    # Game loop
    move_count = 1
    while True:
        # Get user move
        move = input(f"Move {move_count} - Your move: ").strip()

        if move.lower() == 'quit':
            break

        # Make the move
        try:
            result = make_move(session_id, move)
            if not print_feedback(result):
                continue  # Illegal move, try again

            # Check game status
            status = get_session_status(session_id)
            if status.get("is_game_over"):
                print("\n🏁 Game Over!")
                break

            move_count += 1

        except Exception as e:
            print(f"Error: {e}")
            break

    # Final status
    print("\n" + "=" * 60)
    status = get_session_status(session_id)
    print(f"Final position: {status.get('fen')}")
    print(f"Total moves played: {len(status.get('moves', []))}")
    print("Thanks for playing!")


if __name__ == "__main__":
    print("\n⚠️  Make sure the API server is running:")
    print("   uvicorn api_server:app --reload")
    print()
    input("Press Enter to start the game...")
    main()