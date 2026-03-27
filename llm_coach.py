import os
import json
import logging
from typing import Dict, Any, Optional

from env_loader import load_env

load_env()

from move_analysis import severity_from_cp_loss  # noqa: E402 - canonical implementation


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

# ---------------------------------------------------------------------------
# Lazy singleton for the OpenAI async client
# ---------------------------------------------------------------------------
_openai_client = None


def _get_openai_client():
    """Return a shared AsyncOpenAI instance, or None when no key is set."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        _log_missing_key()
        return None
    from openai import AsyncOpenAI
    _openai_client = AsyncOpenAI(
        api_key=api_key,
        base_url=os.getenv("OPENAI_API_ENDPOINT", "https://api.openai.com/v1"),
    )
    return _openai_client


def _log_llm_event(message: str, exc: Optional[Exception] = None) -> None:
    """Log warnings for LLM fallbacks with optional stderr mirroring."""
    if LLM_DEBUG_ENABLED:
        if exc:
            logger.debug("%s: %s", message, exc)
        else:
            logger.debug(message)
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


def rule_basic(move: Dict[str, Any]) -> str:
    cp_loss = float(move.get("cp_loss") or 0.0)
    best = move.get("best_move_san")
    if severity_from_cp_loss(cp_loss) in ("best", "good"):
        return _truncate_words("Solid move. Keep building your plan.", 15)
    if best:
        return _truncate_words(f"Better was {best}. Consider the threats.", 15)
    return _truncate_words("Missed stronger option. Improve piece activity.", 15)


async def coach_move_with_llm(move: Dict[str, Any], level: str = "intermediate", use_llm: bool = True) -> Dict[str, Any]:
    """Attempt to get LLM-generated basic feedback. Fallback to rules on error."""
    MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4")

    result = {
        "basic": rule_basic(move),
        "source": "rules",
    }

    if not use_llm:
        return result

    openai_client = _get_openai_client()
    if openai_client is None:
        return result

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

    try:
        completion = await openai_client.chat.completions.create(
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
        obj["basic"] = _truncate_words(obj.get("basic", result["basic"]) or result["basic"], 50)
        obj["source"] = "llm"
        return obj
    except Exception as e:
        _log_llm_event("LLM fallback to rules after attempting LLM model", e)
    return result
