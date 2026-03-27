"""Shared move analysis utilities.

Extracted from duplicated logic in live_sessions.py, api_server.py, and
analysis_pipeline.py so every caller computes cp-loss, mover-perspective
scores, severity, and MultiPV entries in exactly the same way.
"""

from typing import Any, Dict, List, Optional, Tuple

from config import (
    SEVERITY_BEST_THRESHOLD,
    SEVERITY_GOOD_THRESHOLD,
    SEVERITY_INACCURACY_THRESHOLD,
    SEVERITY_MISTAKE_THRESHOLD,
)


def severity_from_cp_loss(cp_loss_pawns: float) -> str:
    """Classify a centipawn loss (in pawns) into a severity label."""
    cp = abs(cp_loss_pawns)
    if cp <= SEVERITY_BEST_THRESHOLD:
        return "best"
    if cp <= SEVERITY_GOOD_THRESHOLD:
        return "good"
    if cp <= SEVERITY_INACCURACY_THRESHOLD:
        return "inaccuracy"
    if cp <= SEVERITY_MISTAKE_THRESHOLD:
        return "mistake"
    return "blunder"


def compute_mover_perspective_cp(
    before_cp_white: Optional[int],
    after_cp_white: Optional[int],
    mover_is_white: bool,
) -> Tuple[Optional[int], Optional[int]]:
    """Convert white-relative centipawn scores to the mover's perspective.

    Returns (cp_before, cp_after) from the mover's point of view, or
    (None, None) if either input is None.
    """
    if before_cp_white is None or after_cp_white is None:
        return None, None
    if mover_is_white:
        return before_cp_white, after_cp_white
    return -before_cp_white, -after_cp_white


def build_multipv_entries(raw_pv_list: List[Dict[str, Any]], max_line_length: int = 10) -> List[Dict[str, Any]]:
    """Normalise raw MultiPV data from StockfishAnalyzer into API-ready dicts."""
    entries: List[Dict[str, Any]] = []
    for e in raw_pv_list:
        entries.append(
            {
                "move_san": e.get("move_san"),
                "move_uci": e.get("move_uci"),
                "cp": e.get("cp"),
                "mate": e.get("mate"),
                "line_san": e.get("line_san", [])[:max_line_length],
            }
        )
    return entries


def build_move_feedback(
    *,
    move_no: int,
    side: str,
    san: str,
    uci: str,
    fen_before: str,
    fen_after: str,
    eval_before: Dict[str, Any],
    comparison: Dict[str, Any],
    mover_is_white: bool,
) -> Dict[str, Any]:
    """Build a complete move-feedback dict from engine analysis results.

    This is the single source of truth for the feedback structure emitted
    by both the synchronous ``apply_move`` path and the SSE ``stream_move``
    path.
    """
    before_cp_white = eval_before.get("score", {}).get("cp")
    after_cp_white = comparison.get("eval_after", {}).get("score", {}).get("cp")
    cp_before, cp_after = compute_mover_perspective_cp(before_cp_white, after_cp_white, mover_is_white)
    cp_loss = comparison.get("eval_loss", 0.0)
    best_move_san = eval_before.get("best_move_san")
    multipv = build_multipv_entries(eval_before.get("pv", []))

    return {
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
