"""Centralized configuration constants for LLM-ChessCoach.

All magic numbers and tunable thresholds live here so they can be
adjusted in one place and imported by any module that needs them.
"""

import os

# ---------------------------------------------------------------------------
# Severity thresholds (in pawns of centipawn loss)
# ---------------------------------------------------------------------------
SEVERITY_BEST_THRESHOLD = 0.15
SEVERITY_GOOD_THRESHOLD = 0.30
SEVERITY_INACCURACY_THRESHOLD = 0.60
SEVERITY_MISTAKE_THRESHOLD = 1.50

# ---------------------------------------------------------------------------
# Engine defaults
# ---------------------------------------------------------------------------
SSE_QUICK_NODES = 50_000
DEFAULT_ENGINE_DEPTH = 15
DEFAULT_MULTIPV = int(os.getenv("MULTIPV", "5"))
DEFAULT_NODES_PER_PV = int(os.getenv("NODES_PER_PV", "1000000"))

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------
SESSION_TTL_SECONDS = 24 * 60 * 60

# ---------------------------------------------------------------------------
# Accuracy calculation constants (Lichess-style win-percentage model)
# ---------------------------------------------------------------------------
WIN_PERCENTAGE_SCALING = 0.00368208
ACCURACY_BASE = 103.1668
ACCURACY_EXPONENT = -0.04354
ACCURACY_OFFSET = -3.1669

# ---------------------------------------------------------------------------
# Free tier / subscription defaults
# ---------------------------------------------------------------------------
FREE_GAMES_PER_DAY = int(os.getenv("FREE_GAMES_PER_DAY", "1"))
TRIAL_DAYS = int(os.getenv("TRIAL_DAYS", "90"))
APPSTORE_GAMES_PER_PURCHASE = int(os.getenv("APPSTORE_GAMES_PER_PURCHASE", "30"))
SUBSCRIPTION_GAMES_PER_MONTH = int(os.getenv("SUBSCRIPTION_GAMES_PER_MONTH", "100"))
SUBSCRIPTION_ROLLOVER_MONTHS = 2  # credits valid for current month + 2 more

# ---------------------------------------------------------------------------
# Skill level mappings for Stockfish play mode
# ---------------------------------------------------------------------------
SKILL_LEVEL_MAPPINGS = {
    "beginner": {"skill_level": 1, "move_time_ms": 100},
    "adv_beginner": {"skill_level": 2, "move_time_ms": 100},
    "intermediate": {"skill_level": 3, "move_time_ms": 100},
    "advanced": {"skill_level": 4, "move_time_ms": 100},
    "expert": {"skill_level": 6, "move_time_ms": 100},
}
