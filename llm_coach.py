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
    if cp <= 0.15:
        return "best"
    if cp <= 0.3:
        return "good"
    if cp <= 0.60:
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





async def coach_move_with_llm(move: Dict[str, Any], level: str = "intermediate", use_llm: bool = True) -> Dict[str, Any]:
    """Attempt to get LLM-generated basic feedback. Fallback to rules on error.

    move: dict with fields (san, cp_loss, best_move_san, multipv[], fen_before, side, ...)
    """
    # Use OPENAI_API_KEY consistently
    API_KEY = os.getenv("OPENAI_API_KEY")
    API_ENDPOINT = os.getenv("OPENAI_API_ENDPOINT", "https://api.openai.com/v1")
    MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4")

    # Always build safe defaults
    result = {
        "basic": rule_basic(move),
        "source": "rules",
    }

    if not use_llm:
        return result

    if not API_KEY:
        _log_missing_key()
        return result

    from openai import AsyncOpenAI

    # Instantiate async client
    openai_client = AsyncOpenAI(
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
        "return JSON with: basic (<=40 words) "
        f"Player level: {level}. Ground advice in PV; do not contradict engine.\n\n"
        f"Data:\n{json.dumps(structured)}\n\n"
        "Return only a JSON object with keys: basic."
    )


    last_err: Optional[Exception] = None
    try:
        completion = await openai_client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": "You are a concise chess coach that outputs strict JSON."},
                {"role": "user", "content": prompt},
            ],
            extra_body={"reasoning": {"enabled": False}},
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
        obj["basic"] = _truncate_words(obj.get("basic", result["basic"]) or result["basic"], 50)
        obj["source"] = "llm"
        return obj
    except Exception as e:
        last_err = e
    if last_err:
        _log_llm_event("LLM fallback to rules after attempting LLM model", last_err)
    return result
