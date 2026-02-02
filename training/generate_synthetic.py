"""Generate synthetic training data using chess positions and LLM coaching."""

import os
import sys
import json
import asyncio
import argparse
import random
from typing import List, Dict, Any, Optional

# Add parent to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from env_loader import load_env
load_env()

import chess
import chess.pgn
from openai import AsyncOpenAI

from stockfish_engine import StockfishAnalyzer, DEFAULT_MULTIPV
from llm_coach import severity_from_cp_loss, _truncate_words
from training.store import insert_sample, get_sample_count


# Common opening positions to generate training data from
OPENING_POSITIONS = [
    # Italian Game
    "r1bqkbnr/pppp1ppp/2n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
    # Sicilian Defense
    "rnbqkbnr/pp1ppppp/8/2p5/4P3/8/PPPP1PPP/RNBQKBNR w KQkq c6 0 2",
    # French Defense
    "rnbqkbnr/pppp1ppp/4p3/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    # Caro-Kann
    "rnbqkbnr/pp1ppppp/2p5/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
    # Queen's Gambit
    "rnbqkbnr/ppp1pppp/8/3p4/2PP4/8/PP2PPPP/RNBQKBNR b KQkq c3 0 2",
    # King's Indian
    "rnbqkb1r/pppppp1p/5np1/8/2PP4/8/PP2PPPP/RNBQKBNR w KQkq - 0 3",
    # London System
    "rnbqkbnr/ppp1pppp/8/3p4/3P1B2/8/PPP1PPPP/RN1QKBNR b KQkq - 1 2",
    # Ruy Lopez
    "r1bqkbnr/pppp1ppp/2n5/1B2p3/4P3/5N2/PPPP1PPP/RNBQK2R b KQkq - 3 3",
    # Scandinavian
    "rnbqkbnr/ppp1pppp/8/3P4/8/8/PPPP1PPP/RNBQKBNR b KQkq - 0 2",
    # Pirc Defense
    "rnbqkbnr/ppp1pppp/3p4/8/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
]

# Different player levels for diverse training data
PLAYER_LEVELS = ["beginner", "intermediate", "advanced", "expert"]


SYSTEM_PROMPT = "You are a concise chess coach that outputs strict JSON."


def build_coaching_prompt(move_data: Dict[str, Any], level: str) -> str:
    """Build the prompt for coaching."""
    structured = {
        "san": move_data.get("san"),
        "best_move_san": move_data.get("best_move_san"),
        "cp_loss": move_data.get("cp_loss"),
        "side": move_data.get("side"),
        "multipv": move_data.get("multipv", [])[:3],  # Limit for prompt size
    }

    return (
        "You are a concise chess coach. Given a move and engine data, "
        "return JSON with: basic (<=40 words) "
        f"Player level: {level}. Ground advice in PV; do not contradict engine.\n\n"
        f"Data:\n{json.dumps(structured)}\n\n"
        "Return only a JSON object with keys: basic."
    )


async def generate_coaching_response(
    client: AsyncOpenAI,
    move_data: Dict[str, Any],
    level: str,
    model: str,
) -> Optional[str]:
    """Generate a coaching response using the LLM."""
    prompt = build_coaching_prompt(move_data, level)

    try:
        completion = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        content = completion.choices[0].message.content.strip()

        # Clean up markdown code blocks
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        obj = json.loads(content)
        basic = obj.get("basic", "")
        return _truncate_words(basic, 50)

    except Exception as e:
        print(f"  Error generating response: {e}")
        return None


def generate_position_variations(
    base_fen: str,
    num_variations: int = 5,
    max_depth: int = 6,
) -> List[chess.Board]:
    """Generate position variations by playing random legal moves."""
    positions = []

    for _ in range(num_variations):
        board = chess.Board(base_fen)
        depth = random.randint(1, max_depth)

        for _ in range(depth):
            legal_moves = list(board.legal_moves)
            if not legal_moves:
                break
            move = random.choice(legal_moves)
            board.push(move)

        if not board.is_game_over():
            positions.append(board.copy())

    return positions


def analyze_position_for_training(
    board: chess.Board,
    analyzer: StockfishAnalyzer,
) -> List[Dict[str, Any]]:
    """Analyze a position and generate training candidates for each legal move."""
    candidates = []

    # Get the best move analysis first
    eval_before = analyzer.analyze_position(board)
    best_move_san = eval_before.get("best_move_san")
    multipv = eval_before.get("pv", [])

    # Sample some legal moves (not all, to keep it manageable)
    legal_moves = list(board.legal_moves)
    sample_size = min(5, len(legal_moves))
    sampled_moves = random.sample(legal_moves, sample_size)

    # Always include the best move if we found one
    if best_move_san:
        try:
            best_move = board.parse_san(best_move_san)
            if best_move not in sampled_moves:
                sampled_moves.append(best_move)
        except:
            pass

    for move in sampled_moves:
        try:
            san = board.san(move)
            fen_before = board.fen()
            side = "white" if board.turn else "black"

            # Compare move to get cp_loss
            comparison = analyzer.compare_move(board, move)
            cp_loss = comparison.get("eval_loss", 0.0)

            candidates.append({
                "fen_before": fen_before,
                "san": san,
                "best_move_san": best_move_san,
                "cp_loss": cp_loss,
                "side": side,
                "multipv": multipv,
                "severity": severity_from_cp_loss(cp_loss),
            })
        except Exception as e:
            continue

    return candidates


async def generate_training_batch(
    client: AsyncOpenAI,
    positions: List[chess.Board],
    model: str,
    nodes_per_pv: int = 100000,
) -> int:
    """Generate training data for a batch of positions."""
    samples_added = 0

    with StockfishAnalyzer(multipv=DEFAULT_MULTIPV, nodes_per_pv=nodes_per_pv) as analyzer:
        for i, board in enumerate(positions):
            print(f"  Position {i+1}/{len(positions)}: {board.fen()[:50]}...")

            # Analyze position and get move candidates
            candidates = analyze_position_for_training(board, analyzer)

            for candidate in candidates:
                # Pick a random level for diversity
                level = random.choice(PLAYER_LEVELS)

                # Generate coaching response
                response = await generate_coaching_response(
                    client, candidate, level, model
                )

                if response:
                    # Save to database
                    sample_id = insert_sample(
                        fen_before=candidate["fen_before"],
                        san=candidate["san"],
                        best_move_san=candidate["best_move_san"],
                        cp_loss=candidate["cp_loss"],
                        side=candidate["side"],
                        multipv=candidate["multipv"],
                        player_level=level,
                        severity=candidate["severity"],
                        coaching_response=response,
                        source="llm",
                        model_used=model,
                        latency_ms=None,
                    )
                    samples_added += 1
                    print(f"    Added sample {sample_id}: {candidate['san']} ({candidate['severity']})")

                # Small delay to avoid rate limits
                await asyncio.sleep(0.1)

    return samples_added


async def main():
    parser = argparse.ArgumentParser(description="Generate synthetic training data")
    parser.add_argument(
        "--model", "-m",
        default="gpt-5-nano",
        help="Model to use for generating coaching responses",
    )
    parser.add_argument(
        "--positions", "-p",
        type=int,
        default=50,
        help="Number of positions to generate from openings",
    )
    parser.add_argument(
        "--nodes",
        type=int,
        default=100000,
        help="Nodes per PV for Stockfish (lower = faster)",
    )
    parser.add_argument(
        "--api-key",
        help="OpenAI API key (or set OPENAI_API_KEY env var)",
    )

    args = parser.parse_args()

    api_key = args.api_key or os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("Error: No API key. Set OPENAI_API_KEY or use --api-key")
        return

    client = AsyncOpenAI(api_key=api_key)

    print(f"Generating synthetic training data using {args.model}")
    print(f"Starting sample count: {get_sample_count()}")
    print()

    # Generate positions from openings
    all_positions = []
    positions_per_opening = max(1, args.positions // len(OPENING_POSITIONS))

    print(f"Generating {positions_per_opening} variations from each of {len(OPENING_POSITIONS)} openings...")
    for fen in OPENING_POSITIONS:
        positions = generate_position_variations(fen, positions_per_opening)
        all_positions.extend(positions)

    # Shuffle for variety
    random.shuffle(all_positions)
    all_positions = all_positions[:args.positions]

    print(f"Generated {len(all_positions)} unique positions")
    print()

    # Generate training data
    print("Analyzing positions and generating coaching responses...")
    samples_added = await generate_training_batch(
        client, all_positions, args.model, args.nodes
    )

    print()
    print(f"Done! Added {samples_added} training samples")
    print(f"Total samples in database: {get_sample_count()}")


if __name__ == "__main__":
    asyncio.run(main())
