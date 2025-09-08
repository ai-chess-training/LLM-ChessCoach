import uuid
import time
from typing import Dict, Any, Optional, Tuple, List

import chess

from stockfish_engine import StockfishAnalyzer, DEFAULT_MULTIPV, DEFAULT_NODES_PER_PV
from llm_coach import coach_move_with_llm, severity_from_cp_loss


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def create(self, skill_level: str = "intermediate", start_fen: Optional[str] = None) -> Dict[str, Any]:
        sid = str(uuid.uuid4())
        board = chess.Board(start_fen) if start_fen else chess.Board()
        sess = {
            "id": sid,
            "skill_level": skill_level,
            "created_at": time.time(),
            "board": board,
            "moves": [],  # list of move feedback dicts
        }
        self.sessions[sid] = sess
        return {"session_id": sid, "fen_start": board.fen()}

    def get(self, sid: str) -> Dict[str, Any]:
        if sid not in self.sessions:
            raise KeyError("Session not found")
        return self.sessions[sid]

    def _parse_move(self, board: chess.Board, move_str: str) -> Tuple[Optional[chess.Move], Optional[str], Optional[str]]:
        # Try UCI first then SAN
        move = None
        san = None
        uci = None
        try:
            if len(move_str) in (4, 5):
                m = chess.Move.from_uci(move_str)
                if m in board.legal_moves:
                    move = m
        except Exception:
            pass
        if move is None:
            try:
                move = board.parse_san(move_str)
            except Exception:
                return None, None, None
        try:
            san = board.san(move)
        except Exception:
            san = move_str
        uci = move.uci()
        return move, san, uci

    def apply_move(self, sid: str, move_str: str) -> Dict[str, Any]:
        sess = self.get(sid)
        board: chess.Board = sess["board"]
        move, san, uci = self._parse_move(board, move_str)
        if move is None or move not in board.legal_moves:
            return {"legal": False, "error": "Illegal move"}

        fen_before = board.fen()
        move_no = len(sess["moves"]) + 1
        side = "white" if board.turn else "black"

        # Analyze move with MultiPV
        with StockfishAnalyzer(multipv=DEFAULT_MULTIPV, nodes_per_pv=DEFAULT_NODES_PER_PV) as analyzer:
            eval_before = analyzer.analyze_position(board)
            comparison = analyzer.compare_move(board, move)

        # Push the move now
        board.push(move)
        fen_after = board.fen()

        # Derive mover-perspective cp_before/after
        before_cp_white = eval_before.get("score", {}).get("cp")
        eval_after = comparison.get("eval_after", {})
        after_cp_white = eval_after.get("score", {}).get("cp")
        mover_is_white = (side == "white")
        cp_before = None
        cp_after = None
        if before_cp_white is not None and after_cp_white is not None:
            cp_before = before_cp_white if mover_is_white else -before_cp_white
            cp_after = after_cp_white if mover_is_white else -after_cp_white

        cp_loss = comparison.get("eval_loss", 0.0)  # already in pawns, mover perspective
        best_move_san = eval_before.get("best_move_san")

        # Build multipv entries
        multipv_raw: List[Dict[str, Any]] = eval_before.get("pv", [])
        multipv = []
        for e in multipv_raw:
            multipv.append(
                {
                    "move_san": e.get("move_san"),
                    "move_uci": e.get("move_uci"),
                    "cp": e.get("cp"),
                    "mate": e.get("mate"),
                    "line_san": e.get("line_san", [])[:10],
                }
            )

        feedback = {
            "move_no": move_no,
            "side": side,
            "san": san,
            "uci": uci,
            "fen_before": fen_before,
            "fen_after": fen_after,
            "cp_before": cp_before,
            "cp_after": cp_after,
            "cp_loss": cp_loss,
            "severity": severity_from_cp_loss(cp_loss),
            "best_move_san": best_move_san,
            "multipv": multipv,
        }

        # Coach via LLM (with rule-based fallback)
        level = sess.get("skill_level", "intermediate")
        coach = coach_move_with_llm(feedback, level=level)
        feedback.update(
            {
                "basic": coach.get("basic"),
                "extended": coach.get("extended"),
                "tags": coach.get("tags", []),
                "drills": coach.get("drills", []),
                "source": coach.get("source", "rules"),
            }
        )

        sess["moves"].append(feedback)
        return {"legal": True, "feedback": feedback}

    def snapshot(self, sid: str) -> Dict[str, Any]:
        sess = self.get(sid)
        board: chess.Board = sess["board"]
        return {
            "session_id": sid,
            "skill_level": sess.get("skill_level"),
            "fen": board.fen(),
            "moves": sess.get("moves", []),
        }


session_manager = SessionManager()
