"""Utility to load local environment variables for the chess coach backend."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv is declared in requirements
    load_dotenv = None  # type: ignore


@lru_cache(maxsize=1)
def load_env() -> None:
    """Load the first .env-style file we can find near the project root."""
    if os.getenv("ENV_LOADER_DISABLED"):
        return

    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / ".env",
        base_dir / ".env.local",
        base_dir.parent / ".env",
    ]

    if not load_dotenv:
        return

    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)
            break


# Load eagerly so any module importing env_loader gets the variables.
load_env()
