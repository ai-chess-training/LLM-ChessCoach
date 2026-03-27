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

from config import DEFAULT_MULTIPV, DEFAULT_NODES_PER_PV, SKILL_LEVEL_MAPPINGS, SESSION_TTL_SECONDS
from stockfish_engine import StockfishAnalyzer
from move_analysis import build_move_feedback, severity_from_cp_loss
from llm_coach import coach_move_with_llm

logger = logging.getLogger(__name__)

# Session TTL re-exported for backward compat
SESSION_TTL = SESSION_TTL_SECONDS


class SessionManager:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}

    def create(
        self,
        skill_level: str = "intermediate",
        game_mode: str = "play",
        start_fen: Optional[str] = None,
        owner_user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        sid = str(uuid.uuid4())
        board = chess.Board(start_fen) if start_fen else chess.Board()
        skill_config = SKILL_LEVEL_MAPPINGS.get(skill_level, SKILL_LEVEL_MAPPINGS["intermediate"])

        sess = {
            "id": sid,
            "skill_level": skill_level,
            "game_mode": game_mode,
            "engine_skill_level": skill_config["skill_level"],
            "engine_time_ms": skill_config["move_time_ms"],
            "owner_user_id": owner_user_id,
            "game_charged": False,
            "game_charge_event_key": None,
            "created_at": time.time(),
            "board": board,
            "moves": [],
        }
        self.sessions[sid] = sess
        return {
            "session_id": sid,
            "fen_start": board.fen(),
            "game_mode": game_mode,
            "skill_level": skill_level,
        }

    def get(self, sid: str) -> Dict[str, Any]:
        if sid not in self.sessions:
            raise KeyError("Session not found")
        return self.sessions[sid]

    def save(self, sess: Dict[str, Any]) -> None:
        sid = sess.get("id")
        if not sid:
            raise KeyError("Session id missing")
        self.sessions[sid] = sess

    def _get_engine_move(self, sess: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Get engine move for the current position."""
        board: chess.Board = sess["board"]
        skill_level = sess.get("engine_skill_level", 8)
        time_ms = sess.get("engine_time_ms", 2000)

        with StockfishAnalyzer(skill_level=skill_level) as analyzer:
            engine_response = analyzer.get_engine_move(board, time_limit_ms=time_ms)

        if engine_response.get("move_uci"):
            move = chess.Move.from_uci(engine_response["move_uci"])
            board.push(move)
            return {
                "san": engine_response.get("move_san"),
                "uci": engine_response.get("move_uci"),
                "fen_after": board.fen(),
                "score": engine_response.get("score", {}),
            }
        return None

    def _parse_move(self, board: chess.Board, move_str: str) -> Tuple[Optional[chess.Move], Optional[str], Optional[str]]:
        move = None
        san = None
        uci = None
        try:
            if len(move_str) in (4, 5):
                m = chess.Move.from_uci(move_str)
                if m in board.legal_moves:
                    move = m
        except (ValueError, chess.InvalidMoveError):
            pass
        if move is None:
            try:
                move = board.parse_san(move_str)
            except (ValueError, chess.InvalidMoveError):
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
        mover_is_white = (side == "white")

        with StockfishAnalyzer(multipv=DEFAULT_MULTIPV, nodes_per_pv=DEFAULT_NODES_PER_PV) as analyzer:
            eval_before = analyzer.analyze_position(board)
            comparison = analyzer.compare_move(board, move)

        board.push(move)
        fen_after = board.fen()

        feedback = build_move_feedback(
            move_no=move_no, side=side, san=san, uci=uci,
            fen_before=fen_before, fen_after=fen_after,
            eval_before=eval_before, comparison=comparison,
            mover_is_white=mover_is_white,
        )

        level = sess.get("skill_level", "intermediate")
        coach = await coach_move_with_llm(feedback, level=level)
        feedback["basic"] = coach.get("basic")
        feedback["source"] = coach.get("source", "rules")

        sess["moves"].append(feedback)

        engine_move = None
        if sess.get("game_mode") == "play" and not board.is_game_over():
            engine_move = self._get_engine_move(sess)
            if engine_move:
                sess["moves"].append({
                    "move_no": len(sess["moves"]),
                    "side": "white" if board.turn == chess.BLACK else "black",
                    "san": engine_move["san"],
                    "uci": engine_move["uci"],
                    "fen_after": engine_move["fen_after"],
                    "is_engine_move": True,
                })

        self.save(sess)
        return {
            "legal": True,
            "human_feedback": feedback,
            "engine_move": engine_move,
        }

    def undo_last_move(self, sid: str) -> Dict[str, Any]:
        """Pop the last move(s) from the session.

        In play mode, pops both engine response and human move.
        Does NOT refund game credits.
        """
        sess = self.get(sid)
        board: chess.Board = sess["board"]
        moves: list = sess["moves"]

        if not moves:
            raise ValueError("No moves to undo")

        undone = 0
        # In play mode the last entry may be an engine move
        if moves and moves[-1].get("is_engine_move"):
            moves.pop()
            board.pop()
            undone += 1

        # Undo the human move
        if moves:
            moves.pop()
            board.pop()
            undone += 1

        self.save(sess)
        snapshot = self.snapshot(sid)
        snapshot["undone_count"] = undone
        return snapshot

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
            "turn": "white" if board.turn else "black",
        }


class RedisSessionManager(SessionManager):
    """Redis-backed session manager with sliding TTL.

    Sessions are stored in Redis and automatically expire after SESSION_TTL.
    Board state is serialized as FEN + move UCIs so that board.pop() works
    correctly for the undo feature.
    """

    def __init__(self, redis_url: str):
        try:
            self.redis_client = redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            self.redis_client.ping()
            logger.info("Connected to Redis at %s", redis_url)
        except Exception as e:
            logger.error("Failed to connect to Redis: %s", e)
            raise

    def _session_key(self, sid: str) -> str:
        return f"session:{sid}"

    def _serialize_session(self, sess: Dict[str, Any]) -> str:
        """Serialize session to JSON, converting Board to FEN + move stack."""
        serializable = sess.copy()
        if "board" in serializable:
            board: chess.Board = serializable["board"]
            serializable["board_fen"] = board.fen()
            # Persist the full move stack so board.pop() works after deserialization
            serializable["board_move_ucis"] = [m.uci() for m in board.move_stack]
            del serializable["board"]
        return json.dumps(serializable)

    def _deserialize_session(self, data: str) -> Dict[str, Any]:
        """Deserialize session from JSON, rebuilding the Board with full move stack."""
        sess = json.loads(data)
        if "board_fen" in sess:
            move_ucis = sess.pop("board_move_ucis", [])
            # Rebuild board from initial position + replayed moves so pop() works
            if move_ucis:
                board = chess.Board()
                for uci_str in move_ucis:
                    board.push(chess.Move.from_uci(uci_str))
            else:
                board = chess.Board(sess["board_fen"])
            del sess["board_fen"]
            sess["board"] = board
        return sess

    def _refresh_ttl(self, sid: str) -> None:
        try:
            self.redis_client.expire(self._session_key(sid), SESSION_TTL)
        except Exception as e:
            logger.error("Failed to refresh TTL for session %s: %s", sid, e)
            raise

    def create(
        self,
        skill_level: str = "intermediate",
        game_mode: str = "play",
        start_fen: Optional[str] = None,
        owner_user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        sid = str(uuid.uuid4())
        board = chess.Board(start_fen) if start_fen else chess.Board()
        skill_config = SKILL_LEVEL_MAPPINGS.get(skill_level, SKILL_LEVEL_MAPPINGS["intermediate"])

        sess = {
            "id": sid,
            "skill_level": skill_level,
            "game_mode": game_mode,
            "engine_skill_level": skill_config["skill_level"],
            "engine_time_ms": skill_config["move_time_ms"],
            "owner_user_id": owner_user_id,
            "game_charged": False,
            "game_charge_event_key": None,
            "created_at": time.time(),
            "board": board,
            "moves": [],
        }

        try:
            serialized = self._serialize_session(sess)
            self.redis_client.setex(self._session_key(sid), SESSION_TTL, serialized)
            logger.info("Created session %s with %ds TTL", sid, SESSION_TTL)
        except Exception as e:
            logger.error("Failed to create session in Redis: %s", e)
            raise

        return {
            "session_id": sid,
            "fen_start": board.fen(),
            "game_mode": game_mode,
            "skill_level": skill_level,
        }

    def get(self, sid: str) -> Dict[str, Any]:
        try:
            data = self.redis_client.get(self._session_key(sid))
            if data is None:
                raise KeyError("Session not found")
            sess = self._deserialize_session(data)
            self._refresh_ttl(sid)
            return sess
        except redis.RedisError as e:
            logger.error("Redis error retrieving session %s: %s", sid, e)
            raise
        except KeyError:
            raise

    def save(self, sess: Dict[str, Any]) -> None:
        sid = sess.get("id")
        if not sid:
            raise KeyError("Session id missing")
        try:
            serialized = self._serialize_session(sess)
            self.redis_client.setex(self._session_key(sid), SESSION_TTL, serialized)
        except Exception as e:
            logger.error("Failed to save session %s: %s", sid, e)
            raise

    async def apply_move(self, sid: str, move_str: str) -> Dict[str, Any]:
        """Apply move and persist updated session to Redis."""
        return await super().apply_move(sid, move_str)

    def delete(self, sid: str) -> bool:
        try:
            result = self.redis_client.delete(self._session_key(sid))
            return result > 0
        except Exception as e:
            logger.error("Failed to delete session %s: %s", sid, e)
            raise

    def exists(self, sid: str) -> bool:
        try:
            return self.redis_client.exists(self._session_key(sid)) > 0
        except Exception as e:
            logger.error("Failed to check session %s existence: %s", sid, e)
            raise


def _create_session_manager() -> SessionManager:
    """Factory: Redis if REDIS_URL is set, otherwise in-memory."""
    redis_url = os.getenv("REDIS_URL")

    if redis_url and REDIS_AVAILABLE:
        try:
            logger.info("Initializing Redis session storage for multi-worker support")
            return RedisSessionManager(redis_url)
        except Exception as e:
            logger.warning("Failed to initialize Redis session storage: %s", e)
            logger.warning("Falling back to in-memory session storage (NOT suitable for multi-worker)")
            return SessionManager()
    else:
        if not redis_url:
            logger.warning("REDIS_URL not set - using in-memory session storage")
        elif not REDIS_AVAILABLE:
            logger.warning("Redis library not available - install with: pip install redis hiredis")
        return SessionManager()


session_manager = _create_session_manager()
