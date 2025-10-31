import uuid
import time
import os
import json
import logging
from typing import Dict, Any, Optional, Tuple, List

import chess

from env_loader import load_env

load_env()

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

from stockfish_engine import StockfishAnalyzer, DEFAULT_MULTIPV, DEFAULT_NODES_PER_PV, SKILL_LEVEL_MAPPINGS
from llm_coach import coach_move_with_llm, severity_from_cp_loss

logger = logging.getLogger(__name__)

# Session TTL in seconds (24 hours)
SESSION_TTL = 24 * 60 * 60


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def create(self, skill_level: str = "intermediate", game_mode: str = "play", start_fen: Optional[str] = None) -> Dict[str, Any]:
        sid = str(uuid.uuid4())
        board = chess.Board(start_fen) if start_fen else chess.Board()

        # Map skill level to Stockfish configuration
        skill_config = SKILL_LEVEL_MAPPINGS.get(skill_level, SKILL_LEVEL_MAPPINGS["intermediate"])

        sess = {
            "id": sid,
            "skill_level": skill_level,
            "game_mode": game_mode,  # "play" for interactive play, "training" for analysis only
            "engine_skill_level": skill_config["skill_level"],
            "engine_time_ms": skill_config["move_time_ms"],
            "created_at": time.time(),
            "board": board,
            "moves": [],  # list of move feedback dicts
        }
        self.sessions[sid] = sess
        return {
            "session_id": sid,
            "fen_start": board.fen(),
            "game_mode": game_mode,
            "skill_level": skill_level
        }

    def get(self, sid: str) -> Dict[str, Any]:
        if sid not in self.sessions:
            raise KeyError("Session not found")
        return self.sessions[sid]

    def _get_engine_move(self, sess: Dict[str, Any]) -> Dict[str, Any]:
        """Get engine move for the current position."""
        board: chess.Board = sess["board"]
        skill_level = sess.get("engine_skill_level", 8)
        time_ms = sess.get("engine_time_ms", 2000)

        with StockfishAnalyzer(skill_level=skill_level) as analyzer:
            engine_response = analyzer.get_engine_move(board, time_limit_ms=time_ms)

        if engine_response.get("move_uci"):
            # Parse the move
            move = chess.Move.from_uci(engine_response["move_uci"])
            # Apply the move to the board
            board.push(move)

            return {
                "san": engine_response.get("move_san"),
                "uci": engine_response.get("move_uci"),
                "fen_after": board.fen(),
                "score": engine_response.get("score", {})
            }
        return None

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

    async def apply_move(self, sid: str, move_str: str) -> Dict[str, Any]:
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
        coach = await coach_move_with_llm(feedback, level=level)
        feedback.update(
            {
                "basic": coach.get("basic"),
                "extended": coach.get("extended"),
                "source": coach.get("source", "rules"),
            }
        )

        sess["moves"].append(feedback)

        # Get engine move if in play mode
        engine_move = None
        if sess.get("game_mode") == "play" and not board.is_game_over():
            engine_move = self._get_engine_move(sess)
            if engine_move:
                # Store engine move in session history
                engine_feedback = {
                    "move_no": len(sess["moves"]),
                    "side": "white" if board.turn == chess.BLACK else "black",  # After engine move
                    "san": engine_move["san"],
                    "uci": engine_move["uci"],
                    "fen_after": engine_move["fen_after"],
                    "is_engine_move": True
                }
                sess["moves"].append(engine_feedback)

        return {
            "legal": True,
            "human_feedback": feedback,
            "engine_move": engine_move
        }

    def snapshot(self, sid: str) -> Dict[str, Any]:
        sess = self.get(sid)
        board: chess.Board = sess["board"]
        return {
            "session_id": sid,
            "skill_level": sess.get("skill_level"),
            "game_mode": sess.get("game_mode", "training"),
            "fen": board.fen(),
            "moves": sess.get("moves", []),
            "is_game_over": board.is_game_over(),
            "turn": "white" if board.turn else "black"
        }


class RedisSessionManager(SessionManager):
    """
    Redis-backed session manager with sliding TTL.
    Sessions are stored in Redis and automatically expire 24 hours after last access.
    """

    def __init__(self, redis_url: str):
        # Don't call super().__init__() - we don't want the in-memory dict
        try:
            self.redis_client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5
            )
            # Test connection
            self.redis_client.ping()
            logger.info(f"Connected to Redis at {redis_url}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    def _session_key(self, sid: str) -> str:
        """Generate Redis key for session."""
        return f"session:{sid}"

    def _serialize_session(self, sess: Dict[str, Any]) -> str:
        """Serialize session to JSON, converting Board to FEN."""
        serializable = sess.copy()
        if "board" in serializable:
            board: chess.Board = serializable["board"]
            serializable["board_fen"] = board.fen()
            del serializable["board"]
        return json.dumps(serializable)

    def _deserialize_session(self, data: str) -> Dict[str, Any]:
        """Deserialize session from JSON, converting FEN back to Board."""
        sess = json.loads(data)
        if "board_fen" in sess:
            sess["board"] = chess.Board(sess["board_fen"])
            del sess["board_fen"]
        return sess

    def _refresh_ttl(self, sid: str) -> None:
        """Refresh session TTL to 24 hours (sliding window)."""
        try:
            self.redis_client.expire(self._session_key(sid), SESSION_TTL)
        except Exception as e:
            logger.error(f"Failed to refresh TTL for session {sid}: {e}")
            raise

    def create(self, skill_level: str = "intermediate", game_mode: str = "play", start_fen: Optional[str] = None) -> Dict[str, Any]:
        """Create a new session in Redis with 24h TTL."""
        sid = str(uuid.uuid4())
        board = chess.Board(start_fen) if start_fen else chess.Board()

        # Map skill level to Stockfish configuration
        skill_config = SKILL_LEVEL_MAPPINGS.get(skill_level, SKILL_LEVEL_MAPPINGS["intermediate"])

        sess = {
            "id": sid,
            "skill_level": skill_level,
            "game_mode": game_mode,
            "engine_skill_level": skill_config["skill_level"],
            "engine_time_ms": skill_config["move_time_ms"],
            "created_at": time.time(),
            "board": board,
            "moves": [],
        }

        try:
            # Store in Redis with 24h TTL
            serialized = self._serialize_session(sess)
            self.redis_client.setex(self._session_key(sid), SESSION_TTL, serialized)
            logger.info(f"Created session {sid} with {SESSION_TTL}s TTL")
        except Exception as e:
            logger.error(f"Failed to create session in Redis: {e}")
            raise

        return {
            "session_id": sid,
            "fen_start": board.fen(),
            "game_mode": game_mode,
            "skill_level": skill_level
        }

    def get(self, sid: str) -> Dict[str, Any]:
        """Retrieve session from Redis and refresh TTL."""
        try:
            data = self.redis_client.get(self._session_key(sid))
            if data is None:
                raise KeyError("Session not found")

            # Deserialize session
            sess = self._deserialize_session(data)

            # Refresh TTL (sliding window)
            self._refresh_ttl(sid)

            return sess
        except redis.RedisError as e:
            logger.error(f"Redis error retrieving session {sid}: {e}")
            raise
        except KeyError:
            raise
        except Exception as e:
            logger.error(f"Error retrieving session {sid}: {e}")
            raise

    async def apply_move(self, sid: str, move_str: str) -> Dict[str, Any]:
        """Apply move to session and update Redis with refreshed TTL."""
        # Get session (this also refreshes TTL)
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

        cp_loss = comparison.get("eval_loss", 0.0)
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
        coach = await coach_move_with_llm(feedback, level=level)
        feedback.update(
            {
                "basic": coach.get("basic"),
                "extended": coach.get("extended"),
                "source": coach.get("source", "rules"),
            }
        )

        sess["moves"].append(feedback)

        # Get engine move if in play mode
        engine_move = None
        if sess.get("game_mode") == "play" and not board.is_game_over():
            engine_move = self._get_engine_move(sess)
            if engine_move:
                # Store engine move in session history
                engine_feedback = {
                    "move_no": len(sess["moves"]),
                    "side": "white" if board.turn == chess.BLACK else "black",
                    "san": engine_move["san"],
                    "uci": engine_move["uci"],
                    "fen_after": engine_move["fen_after"],
                    "is_engine_move": True
                }
                sess["moves"].append(engine_feedback)

        # Update session in Redis with refreshed TTL
        try:
            serialized = self._serialize_session(sess)
            self.redis_client.setex(self._session_key(sid), SESSION_TTL, serialized)
            logger.debug(f"Updated session {sid} and refreshed TTL")
        except Exception as e:
            logger.error(f"Failed to update session {sid} in Redis: {e}")
            raise

        return {
            "legal": True,
            "human_feedback": feedback,
            "engine_move": engine_move
        }

    def delete(self, sid: str) -> bool:
        """Explicitly delete a session from Redis."""
        try:
            result = self.redis_client.delete(self._session_key(sid))
            return result > 0
        except Exception as e:
            logger.error(f"Failed to delete session {sid}: {e}")
            raise

    def exists(self, sid: str) -> bool:
        """Check if session exists in Redis."""
        try:
            return self.redis_client.exists(self._session_key(sid)) > 0
        except Exception as e:
            logger.error(f"Failed to check session {sid} existence: {e}")
            raise


def _create_session_manager() -> SessionManager:
    """
    Factory function to create appropriate session manager.
    Uses Redis if REDIS_URL is set, otherwise falls back to in-memory.
    """
    redis_url = os.getenv("REDIS_URL")

    if redis_url and REDIS_AVAILABLE:
        try:
            logger.info("Initializing Redis session storage for multi-worker support")
            return RedisSessionManager(redis_url)
        except Exception as e:
            logger.warning(f"Failed to initialize Redis session storage: {e}")
            logger.warning("Falling back to in-memory session storage (NOT suitable for multi-worker)")
            return SessionManager()
    else:
        if not redis_url:
            logger.warning("REDIS_URL not set - using in-memory session storage")
            logger.warning("This is NOT suitable for multi-worker deployments")
        elif not REDIS_AVAILABLE:
            logger.warning("Redis library not available - install with: pip install redis hiredis")
            logger.warning("Falling back to in-memory session storage")
        return SessionManager()


session_manager = _create_session_manager()
