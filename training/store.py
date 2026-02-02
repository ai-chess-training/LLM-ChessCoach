"""SQLite storage for training data collection."""

import os
import sqlite3
import json
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from env_loader import load_env

load_env()

DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "training.db")
DB_PATH = os.getenv("TRAINING_DB_PATH", DEFAULT_DB_PATH)


def _get_db_path() -> str:
    """Get database path, creating parent directories if needed."""
    path = os.path.abspath(DB_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


@contextmanager
def get_connection():
    """Context manager for database connections."""
    conn = sqlite3.connect(_get_db_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Initialize database schema."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS coaching_samples (
                id INTEGER PRIMARY KEY,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

                -- Input context
                fen_before TEXT,
                san TEXT,
                best_move_san TEXT,
                cp_loss REAL,
                side TEXT,
                multipv_json TEXT,
                player_level TEXT,
                severity TEXT,

                -- Output
                coaching_response TEXT,
                source TEXT,

                -- Metadata
                model_used TEXT,
                latency_ms INTEGER,

                -- Annotation
                quality_rating INTEGER,
                flagged INTEGER DEFAULT 0,
                notes TEXT
            );

            CREATE TABLE IF NOT EXISTS models (
                id TEXT PRIMARY KEY,
                provider TEXT,
                base_model TEXT,
                fine_tuned_model_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                training_samples INTEGER,
                status TEXT,
                metrics_json TEXT,
                is_active INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_samples_created ON coaching_samples(created_at);
            CREATE INDEX IF NOT EXISTS idx_samples_source ON coaching_samples(source);
            CREATE INDEX IF NOT EXISTS idx_samples_quality ON coaching_samples(quality_rating);
            CREATE INDEX IF NOT EXISTS idx_models_active ON models(is_active);
        """)


def insert_sample(
    fen_before: Optional[str],
    san: str,
    best_move_san: Optional[str],
    cp_loss: Optional[float],
    side: str,
    multipv: Optional[List[Dict]],
    player_level: str,
    severity: str,
    coaching_response: str,
    source: str,
    model_used: str,
    latency_ms: Optional[int] = None,
) -> int:
    """Insert a coaching sample into the database."""
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO coaching_samples (
                fen_before, san, best_move_san, cp_loss, side,
                multipv_json, player_level, severity,
                coaching_response, source, model_used, latency_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                fen_before,
                san,
                best_move_san,
                cp_loss,
                side,
                json.dumps(multipv) if multipv else None,
                player_level,
                severity,
                coaching_response,
                source,
                model_used,
                latency_ms,
            ),
        )
        return cursor.lastrowid


def get_samples(
    min_quality: Optional[int] = None,
    source: Optional[str] = None,
    exclude_flagged: bool = True,
    limit: Optional[int] = None,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Retrieve coaching samples with optional filtering."""
    conditions = []
    params = []

    if min_quality is not None:
        conditions.append("quality_rating >= ?")
        params.append(min_quality)

    if source is not None:
        conditions.append("source = ?")
        params.append(source)

    if exclude_flagged:
        conditions.append("flagged = 0")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    query = f"""
        SELECT * FROM coaching_samples
        WHERE {where_clause}
        ORDER BY created_at DESC
    """

    if limit is not None:
        query += f" LIMIT {limit} OFFSET {offset}"

    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def get_sample_count(
    min_quality: Optional[int] = None,
    source: Optional[str] = None,
    exclude_flagged: bool = True,
) -> int:
    """Count coaching samples with optional filtering."""
    conditions = []
    params = []

    if min_quality is not None:
        conditions.append("quality_rating >= ?")
        params.append(min_quality)

    if source is not None:
        conditions.append("source = ?")
        params.append(source)

    if exclude_flagged:
        conditions.append("flagged = 0")

    where_clause = " AND ".join(conditions) if conditions else "1=1"

    with get_connection() as conn:
        result = conn.execute(
            f"SELECT COUNT(*) FROM coaching_samples WHERE {where_clause}",
            params,
        ).fetchone()
        return result[0]


def update_sample_quality(sample_id: int, quality_rating: int, notes: Optional[str] = None):
    """Update quality rating for a sample."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE coaching_samples SET quality_rating = ?, notes = ? WHERE id = ?",
            (quality_rating, notes, sample_id),
        )


def flag_sample(sample_id: int, flagged: bool = True, notes: Optional[str] = None):
    """Flag or unflag a sample."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE coaching_samples SET flagged = ?, notes = ? WHERE id = ?",
            (1 if flagged else 0, notes, sample_id),
        )


def insert_model(
    model_id: str,
    provider: str,
    base_model: str,
    fine_tuned_model_id: Optional[str] = None,
    training_samples: Optional[int] = None,
    status: str = "pending",
    metrics: Optional[Dict] = None,
) -> str:
    """Insert a model record."""
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO models (
                id, provider, base_model, fine_tuned_model_id,
                training_samples, status, metrics_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_id,
                provider,
                base_model,
                fine_tuned_model_id,
                training_samples,
                status,
                json.dumps(metrics) if metrics else None,
            ),
        )
        return model_id


def get_model(model_id: str) -> Optional[Dict[str, Any]]:
    """Get a model by ID."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM models WHERE id = ?", (model_id,)
        ).fetchone()
        return dict(row) if row else None


def get_active_model() -> Optional[Dict[str, Any]]:
    """Get the currently active model."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM models WHERE is_active = 1"
        ).fetchone()
        return dict(row) if row else None


def set_active_model(model_id: str):
    """Set a model as the active one (deactivates others)."""
    with get_connection() as conn:
        conn.execute("UPDATE models SET is_active = 0")
        conn.execute(
            "UPDATE models SET is_active = 1 WHERE id = ?", (model_id,)
        )


def update_model_status(model_id: str, status: str, metrics: Optional[Dict] = None):
    """Update model training status."""
    with get_connection() as conn:
        if metrics:
            conn.execute(
                "UPDATE models SET status = ?, metrics_json = ? WHERE id = ?",
                (status, json.dumps(metrics), model_id),
            )
        else:
            conn.execute(
                "UPDATE models SET status = ? WHERE id = ?",
                (status, model_id),
            )


def list_models(status: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all models, optionally filtered by status."""
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM models WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM models ORDER BY created_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]


# Initialize database on import
init_db()
