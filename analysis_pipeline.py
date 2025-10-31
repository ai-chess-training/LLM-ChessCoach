from typing import Dict, Any, List, Optional
import io
import asyncio
import chess
import chess.pgn

from stockfish_engine import StockfishAnalyzer, DEFAULT_MULTIPV, DEFAULT_NODES_PER_PV
from llm_coach import coach_move_with_llm, severity_from_cp_loss


def _safe_read_game(pgn: str) -> Optional[chess.pgn.Game]:
    try:
        game = chess.pgn.read_game(io.StringIO(pgn))
        return game
    except Exception:
        return None


async def analyze_pgn_to_feedback(
    pgn_content: str,
    level: str = "intermediate",
    max_plies: Optional[int] = None,
    use_llm: bool = True,
    llm_mode: str = "all",
) -> Optional[Dict[str, Any]]:
    game = _safe_read_game(pgn_content)
    if not game:
        return None

    board = game.board()
    moves_feedback: List[Dict[str, Any]] = []

    with StockfishAnalyzer(multipv=DEFAULT_MULTIPV, nodes_per_pv=DEFAULT_NODES_PER_PV) as analyzer:
        move_no = 0
        for node in game.mainline():
            move = node.move
            side = "white" if board.turn else "black"
            fen_before = board.fen()
            san = board.san(move)
            eval_before = analyzer.analyze_position(board)
            comparison = analyzer.compare_move(board, move)
            board.push(move)
            fen_after = board.fen()

            before_cp_white = eval_before.get("score", {}).get("cp")
            after_cp_white = comparison.get("eval_after", {}).get("score", {}).get("cp")
            mover_is_white = (side == "white")
            cp_before = before_cp_white if mover_is_white else (-before_cp_white if before_cp_white is not None else None)
            cp_after = after_cp_white if mover_is_white else (-after_cp_white if after_cp_white is not None else None)
            cp_loss = comparison.get("eval_loss", 0.0)
            best_move_san = eval_before.get("best_move_san")
            multipv = eval_before.get("pv", [])

            payload = {
                "move_no": (move_no // 2) + 1,
                "side": side,
                "san": san,
                "uci": move.uci(),
                "fen_before": fen_before,
                "fen_after": fen_after,
                "cp_before": cp_before,
                "cp_after": cp_after,
                "cp_loss": cp_loss,
                "severity": severity_from_cp_loss(cp_loss),
                "best_move_san": best_move_san,
                "multipv": multipv,
            }
            # Decide whether to invoke LLM for this move
            enable_for_move = use_llm and (llm_mode == "all" or payload["severity"] in ("mistake", "blunder"))
            coach = await coach_move_with_llm(payload, level=level, use_llm=enable_for_move)
            payload.update(
                {
                    "basic": coach.get("basic"),
                    "extended": coach.get("extended"),
                    "source": coach.get("source", "rules"),
                }
            )
            moves_feedback.append(payload)
            move_no += 1
            if max_plies is not None and move_no >= max_plies:
                break

    # Summaries (simple ACPL and counts)
    def _side_stats(side: str) -> Dict[str, Any]:
        side_moves = [m for m in moves_feedback if m["side"] == side]
        acpl = None
        if side_moves:
            cp_losses = [abs(m.get("cp_loss") or 0.0) * 100 for m in side_moves]  # to centipawns
            acpl = sum(cp_losses) / len(cp_losses) if cp_losses else 0
        best_rate = 0.0
        if side_moves:
            best_count = sum(1 for m in side_moves if m.get("severity") in ("best", "good"))
            best_rate = best_count * 100.0 / len(side_moves)
        mistakes = sum(1 for m in side_moves if m.get("severity") == "mistake")
        blunders = sum(1 for m in side_moves if m.get("severity") == "blunder")
        return {
            "acpl": (acpl / 100.0) if acpl is not None else None,  # pawns
            "best_move_rate": best_rate,
            "mistakes": mistakes,
            "blunders": blunders,
        }

    w = _side_stats("white")
    b = _side_stats("black")

    summary = {
        "moves": moves_feedback,
        "acpl_white": w.get("acpl"),
        "acpl_black": b.get("acpl"),
        "best_move_rate_white": w.get("best_move_rate"),
        "best_move_rate_black": b.get("best_move_rate"),
        "mistakes_white": w.get("mistakes"),
        "mistakes_black": b.get("mistakes"),
        "blunders_white": w.get("blunders"),
        "blunders_black": b.get("blunders"),
        "openings": [game.headers.get("Opening", "Unknown")],
        "critical_positions": [i + 1 for i, m in enumerate(moves_feedback) if m.get("severity") in ("mistake", "blunder")],
    }
    return summary
