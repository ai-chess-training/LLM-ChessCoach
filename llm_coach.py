import os
import json
import logging
import time
from typing import Dict, Any, List, Optional

from env_loader import load_env

load_env()


logger = logging.getLogger(__name__)
_MISSING_KEY_LOGGED = False
LLM_DEBUG_ENABLED = os.getenv("LLM_DEBUG") == "1"


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


LLM_REQUEST_TIMEOUT_SECONDS = max(0.5, _env_float("LLM_TIMEOUT_SECONDS", _env_float("LLM_TIMEOUT", 8.0)))
LLM_TOTAL_TIMEOUT_SECONDS = max(LLM_REQUEST_TIMEOUT_SECONDS, _env_float("LLM_TOTAL_TIMEOUT_SECONDS", 12.0))


def _log_llm_event(message: str, exc: Optional[Exception] = None) -> None:
    """Log warnings for LLM fallbacks with optional stderr mirroring."""
    if LLM_DEBUG_ENABLED:
        if exc:
            print(f"{message}: {exc}")
        else:
            print(message)
    if exc:
        logger.warning(message, exc_info=exc)
    else:
        logger.warning(message)


def _log_missing_key() -> None:
    global _MISSING_KEY_LOGGED
    if _MISSING_KEY_LOGGED:
        return
    _MISSING_KEY_LOGGED = True
    _log_llm_event("OPENAI_API_KEY not set; using rule-based coaching fallback.")


def _truncate_words(text: str, max_words: int) -> str:
    words = text.strip().split()
    if len(words) <= max_words:
        return text.strip()
    return " ".join(words[:max_words])


def severity_from_cp_loss(cp_loss_pawns: float) -> str:
    # Tunable thresholds (in pawns)
    cp = abs(cp_loss_pawns)
    if cp <= 0.05:
        return "best"
    if cp <= 0.20:
        return "good"
    if cp <= 0.50:
        return "inaccuracy"
    if cp <= 1.50:
        return "mistake"
    return "blunder"


def rule_basic(move: Dict[str, Any]) -> str:
    # Simple one-liners under 15 words
    cp_loss = float(move.get("cp_loss") or 0.0)
    best = move.get("best_move_san")
    side = move.get("side")
    if severity_from_cp_loss(cp_loss) in ("best", "good"):
        return _truncate_words("Solid move. Keep building your plan.", 15)
    if best:
        return _truncate_words(f"Better was {best}. Consider the threats.", 15)
    return _truncate_words("Missed stronger option. Improve piece activity.", 15)


def rule_extended(move: Dict[str, Any]) -> str:
    # Concise extended feedback under 100 words
    san = move.get("san")
    best = move.get("best_move_san")
    cp_loss = float(move.get("cp_loss") or 0.0)
    multipv: List[Dict[str, Any]] = move.get("multipv") or []
    best_line = (multipv[0]["line_san"] if multipv else [])
    why = "This improves piece activity and reduces tactical weaknesses."
    if cp_loss >= 0.5:
        why = "This line protects against threats and gains a positional edge."
    text = (
        f"You played {san}. Engine prefers {best}. "
        f"Evaluation worsened by {cp_loss:.2f} pawns. "
        f"Main line: {' '.join(best_line[:8])}. {why}"
    )
    return _truncate_words(text, 100)


def make_drills(move: Dict[str, Any]) -> List[Dict[str, Any]]:
    drills: List[Dict[str, Any]] = []
    severity = severity_from_cp_loss(float(move.get("cp_loss") or 0.0))
    if severity in ("mistake", "blunder", "inaccuracy"):
        fen = move.get("fen_before")
        side = move.get("side")
        multipv: List[Dict[str, Any]] = move.get("multipv") or []
        best_line = multipv[0]["line_san"] if multipv else []
        alt = multipv[1]["line_san"] if len(multipv) > 1 else []
        objective = "Find the best continuation"
        if len(best_line) >= 1 and ("#" in " ".join(best_line) or "+" in " ".join(best_line)):
            objective = "Convert advantage: find forcing line"
        drills.append(
            {
                "fen": fen,
                "side_to_move": side,
                "objective": objective,
                "best_line_san": best_line[:12],
                "alt_traps_san": alt[:8],
            }
        )
    return drills


def coach_move_with_llm(move: Dict[str, Any], level: str = "intermediate", use_llm: bool = True) -> Dict[str, Any]:
    """Attempt to get LLM-generated basic/extended and drills. Fallback to rules on error.

    move: dict with fields (san, cp_loss, best_move_san, multipv[], fen_before, side, ...)
    """
    API_KEY = os.getenv("AI_API_KEY")
    API_ENDPOINT = os.getenv("AI_API_ENDPOINT")
    MODEL_NAME = os.getenv("AI_MODEL_NAME")

    # Always build safe defaults
    result = {
        "basic": rule_basic(move),
        "extended": rule_extended(move),
        "drills": make_drills(move),
        "tags": [],
        "source": "rules",
    }

    if not use_llm:
        return result

    if not API_KEY:
        _log_missing_key()
        return result

    from openai import OpenAI

    # Instantiate client without kwargs for broader compatibility across SDK versions
    openai_client = OpenAI(
        api_key=API_KEY,
        base_url=API_ENDPOINT,
    )
    structured = {
        "san": move.get("san"),
        "best_move_san": move.get("best_move_san"),
        "cp_loss": move.get("cp_loss"),
        "side": move.get("side"),
        "multipv": move.get("multipv", []),
    }

    prompt = (
        "You are a concise chess coach. Given a move and engine data, "
        "return JSON with: basic (<=15 words), extended (<=100 words), "
        "tags (array), and drills (array of {objective, best_line_san}). "
        f"Player level: {level}. Ground advice in PV; do not contradict engine.\n\n"
        f"Data:\n{json.dumps(structured)}\n\n"
        "Return only a JSON object with keys: basic, extended, tags, drills."
    )


    last_err: Optional[Exception] = None
    try:
        completion = openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "You are a concise chess coach that outputs strict JSON."},
                {"role": "user", "content": prompt},
            ],
        )
        content = completion.choices[0].message.content.strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]
        obj = json.loads(content)
        # Enforce length limits
        obj["basic"] = _truncate_words(obj.get("basic", result["basic"]) or result["basic"], 15)
        obj["extended"] = _truncate_words(obj.get("extended", result["extended"]) or result["extended"], 100)
        drills = obj.get("drills") or []
        # Normalize drills structure
        normalized_drills = []
        for d in drills[:2]:
            normalized_drills.append(
                {
                    "fen": move.get("fen_before"),
                    "side_to_move": move.get("side"),
                    "objective": d.get("objective") or "Find the best continuation",
                    "best_line_san": d.get("best_line_san") or [],
                    "alt_traps_san": d.get("alt_traps_san") or [],
                }
            )
        obj["drills"] = normalized_drills or result["drills"]
        obj["tags"] = obj.get("tags") or []
        obj["source"] = "llm"
        return obj
    except Exception as e:
        last_err = e
    if last_err:
        _log_llm_event("LLM fallback to rules after attempting LLM model", last_err)
    return result
