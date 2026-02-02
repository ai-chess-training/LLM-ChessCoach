"""Async collector for coaching samples."""

import os
import logging
import asyncio
from typing import Dict, Any, Optional

from env_loader import load_env

load_env()

logger = logging.getLogger(__name__)

# Check if collection is enabled
COLLECTION_ENABLED = os.getenv("ENABLE_TRAINING_COLLECTION", "0") == "1"


async def log_coaching_sample(
    move: Dict[str, Any],
    level: str,
    response: Dict[str, Any],
    model: str,
    latency_ms: Optional[int] = None,
) -> Optional[int]:
    """
    Log a coaching sample to the training database.

    This function is designed to be called with asyncio.create_task()
    so it doesn't block the main request path.

    Args:
        move: The move data dict (san, best_move_san, cp_loss, etc.)
        level: Player skill level
        response: The coaching response dict (basic, source)
        model: Model name used
        latency_ms: Request latency in milliseconds

    Returns:
        Sample ID if inserted, None if collection disabled or error
    """
    if not COLLECTION_ENABLED:
        return None

    try:
        # Import here to avoid circular imports and lazy-load the DB
        from training.store import insert_sample

        # Run the blocking DB insert in a thread pool
        loop = asyncio.get_event_loop()
        sample_id = await loop.run_in_executor(
            None,
            lambda: insert_sample(
                fen_before=move.get("fen_before"),
                san=move.get("san", ""),
                best_move_san=move.get("best_move_san"),
                cp_loss=move.get("cp_loss"),
                side=move.get("side", ""),
                multipv=move.get("multipv"),
                player_level=level,
                severity=move.get("severity", ""),
                coaching_response=response.get("basic", ""),
                source=response.get("source", "unknown"),
                model_used=model,
                latency_ms=latency_ms,
            ),
        )
        logger.debug(f"Logged coaching sample {sample_id}")
        return sample_id

    except Exception as e:
        # Never let collection errors break the main flow
        logger.error(f"Failed to log coaching sample: {e}")
        return None


def is_collection_enabled() -> bool:
    """Check if training data collection is enabled."""
    return COLLECTION_ENABLED
