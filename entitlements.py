from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Generator, Optional
from urllib.parse import urlparse


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _parse_timestamp(value: Optional[str]) -> datetime:
    if not value:
        return utc_now()
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_date_str(value: datetime) -> str:
    return value.astimezone(timezone.utc).date().isoformat()


def _default_database_url() -> str:
    return "sqlite:////tmp/llm_chesscoach_entitlements.db"


def _free_games_per_day() -> int:
    return int(os.getenv("FREE_GAMES_PER_DAY", "5"))


def _trial_days() -> int:
    return int(os.getenv("TRIAL_DAYS", "14"))


def _app_store_games_pack() -> int:
    return int(os.getenv("APPSTORE_GAMES_PER_PURCHASE", "30"))


@dataclass
class UserRecord:
    id: int
    apple_sub: str
    apple_email: Optional[str]
    created_at: str
    last_login_at: str


@dataclass
class EntitlementSnapshot:
    user_id: int
    trial_started_at: str
    trial_ends_at: str
    trial_active: bool
    daily_free_limit: int
    daily_free_used: int
    daily_free_remaining: int
    paid_games_balance: int
    total_available_games: int
    can_play: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class UsageResult:
    consumed: bool
    charge_kind: str
    snapshot: EntitlementSnapshot


@dataclass
class PurchaseResult:
    applied: bool
    revoked: bool
    already_processed: bool
    games_changed: int
    snapshot: EntitlementSnapshot


class EntitlementError(ValueError):
    def __init__(self, snapshot: EntitlementSnapshot, code: str = "entitlement_exhausted"):
        super().__init__(code)
        self.snapshot = snapshot
        self.code = code

    def to_payload(self) -> Dict[str, Any]:
        payload = self.snapshot.to_dict()
        payload["code"] = self.code
        return payload


class DatabaseConfigurationError(RuntimeError):
    """Raised when the entitlement store cannot be configured."""


class EntitlementStore:
    def __init__(self, database_url: Optional[str] = None):
        self.database_url = (database_url or os.getenv("DATABASE_URL") or "").strip() or _default_database_url()
        self._backend = self._detect_backend(self.database_url)
        self._sqlite_path = self._resolve_sqlite_path(self.database_url) if self._backend == "sqlite" else None
        self._ensure_schema()

    @staticmethod
    def _detect_backend(database_url: str) -> str:
        if database_url.startswith("sqlite://"):
            return "sqlite"
        if database_url.startswith("postgres://") or database_url.startswith("postgresql://"):
            return "postgres"
        raise DatabaseConfigurationError(f"Unsupported DATABASE_URL scheme: {database_url}")

    @staticmethod
    def _resolve_sqlite_path(database_url: str) -> str:
        parsed = urlparse(database_url)
        path = parsed.path or ""
        if path in ("", "/"):
            return "/tmp/llm_chesscoach_entitlements.db"
        if path == "/:memory:":
            return ":memory:"
        return path

    def _ensure_schema(self) -> None:
        with self._connection(write=True) as conn:
            for statement in self._schema_statements():
                self._execute(conn, statement)
            self._commit(conn)

    def _schema_statements(self) -> list[str]:
        if self._backend == "sqlite":
            return [
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    apple_sub TEXT NOT NULL UNIQUE,
                    apple_email TEXT,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT NOT NULL
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS user_entitlements (
                    user_id INTEGER PRIMARY KEY,
                    trial_started_at TEXT NOT NULL,
                    paid_games_balance INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS daily_usage (
                    user_id INTEGER NOT NULL,
                    usage_date TEXT NOT NULL,
                    used_games INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (user_id, usage_date),
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS usage_events (
                    event_key TEXT PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    source TEXT NOT NULL,
                    charge_kind TEXT NOT NULL,
                    usage_date TEXT NOT NULL,
                    games_delta INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """,
                """
                CREATE TABLE IF NOT EXISTS app_store_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    original_transaction_id TEXT,
                    user_id INTEGER NOT NULL,
                    product_id TEXT NOT NULL,
                    environment TEXT NOT NULL,
                    signed_transaction_info TEXT NOT NULL,
                    status TEXT NOT NULL,
                    games_granted INTEGER NOT NULL,
                    notification_type TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """,
            ]
        return [
            """
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY,
                apple_sub TEXT NOT NULL UNIQUE,
                apple_email TEXT,
                created_at TEXT NOT NULL,
                last_login_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_entitlements (
                user_id BIGINT PRIMARY KEY REFERENCES users(id),
                trial_started_at TEXT NOT NULL,
                paid_games_balance INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS daily_usage (
                user_id BIGINT NOT NULL REFERENCES users(id),
                usage_date TEXT NOT NULL,
                used_games INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, usage_date)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS usage_events (
                event_key TEXT PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(id),
                source TEXT NOT NULL,
                charge_kind TEXT NOT NULL,
                usage_date TEXT NOT NULL,
                games_delta INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS app_store_transactions (
                transaction_id TEXT PRIMARY KEY,
                original_transaction_id TEXT,
                user_id BIGINT NOT NULL REFERENCES users(id),
                product_id TEXT NOT NULL,
                environment TEXT NOT NULL,
                signed_transaction_info TEXT NOT NULL,
                status TEXT NOT NULL,
                games_granted INTEGER NOT NULL,
                notification_type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ]

    @contextmanager
    def _connection(self, write: bool = False) -> Generator[Any, None, None]:
        if self._backend == "sqlite":
            conn = sqlite3.connect(self._sqlite_path, timeout=30, isolation_level=None, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            try:
                if write:
                    conn.execute("BEGIN IMMEDIATE")
                yield conn
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            return

        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - exercised only when optional dependency is absent
            raise DatabaseConfigurationError("psycopg is required for PostgreSQL entitlement storage") from exc

        conn = psycopg.connect(self.database_url)
        try:
            conn.execute("BEGIN")
            yield conn
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _sql(self, query: str) -> str:
        if self._backend == "postgres":
            return query.replace("?", "%s")
        return query

    def _execute(self, conn: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
        cur = conn.cursor()
        cur.execute(self._sql(query), params)
        return cur

    def _row_to_dict(self, cur: Any, row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        if isinstance(row, sqlite3.Row):
            return dict(row)
        description = cur.description or []
        return {description[idx].name: value for idx, value in enumerate(row)}

    def _fetchone(self, conn: Any, query: str, params: tuple[Any, ...] = ()) -> Optional[Dict[str, Any]]:
        cur = self._execute(conn, query, params)
        row = cur.fetchone()
        return self._row_to_dict(cur, row)

    def _commit(self, conn: Any) -> None:
        conn.commit()

    def _ensure_entitlement_row(self, conn: Any, user_id: int, now: datetime) -> None:
        now_iso = _isoformat(now)
        self._execute(
            conn,
            """
            INSERT INTO user_entitlements (user_id, trial_started_at, paid_games_balance, updated_at)
            VALUES (?, ?, 0, ?)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (user_id, now_iso, now_iso),
        )

    def upsert_user(self, apple_sub: str, apple_email: Optional[str] = None, now: Optional[datetime] = None) -> UserRecord:
        current_time = now or utc_now()
        now_iso = _isoformat(current_time)
        with self._connection(write=True) as conn:
            self._execute(
                conn,
                """
                INSERT INTO users (apple_sub, apple_email, created_at, last_login_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (apple_sub) DO UPDATE SET
                    apple_email = COALESCE(excluded.apple_email, users.apple_email),
                    last_login_at = excluded.last_login_at
                """,
                (apple_sub, apple_email, now_iso, now_iso),
            )
            row = self._fetchone(conn, "SELECT * FROM users WHERE apple_sub = ?", (apple_sub,))
            self._ensure_entitlement_row(conn, int(row["id"]), current_time)
            self._commit(conn)
        return UserRecord(
            id=int(row["id"]),
            apple_sub=str(row["apple_sub"]),
            apple_email=row.get("apple_email"),
            created_at=str(row["created_at"]),
            last_login_at=str(row["last_login_at"]),
        )

    def get_user_by_id(self, user_id: int) -> Optional[UserRecord]:
        with self._connection() as conn:
            row = self._fetchone(conn, "SELECT * FROM users WHERE id = ?", (user_id,))
        if not row:
            return None
        return UserRecord(
            id=int(row["id"]),
            apple_sub=str(row["apple_sub"]),
            apple_email=row.get("apple_email"),
            created_at=str(row["created_at"]),
            last_login_at=str(row["last_login_at"]),
        )

    def get_transaction(self, transaction_id: str) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            return self._fetchone(conn, "SELECT * FROM app_store_transactions WHERE transaction_id = ?", (transaction_id,))

    def get_entitlement_snapshot(self, user_id: int, now: Optional[datetime] = None) -> EntitlementSnapshot:
        current_time = now or utc_now()
        with self._connection() as conn:
            return self._read_snapshot(conn, user_id, current_time)

    def _lock_entitlement(self, conn: Any, user_id: int, now: datetime) -> Dict[str, Any]:
        self._ensure_entitlement_row(conn, user_id, now)
        if self._backend == "postgres":
            row = self._fetchone(
                conn,
                """
                SELECT user_id, trial_started_at, paid_games_balance, updated_at
                FROM user_entitlements
                WHERE user_id = ?
                FOR UPDATE
                """,
                (user_id,),
            )
        else:
            row = self._fetchone(
                conn,
                "SELECT user_id, trial_started_at, paid_games_balance, updated_at FROM user_entitlements WHERE user_id = ?",
                (user_id,),
            )
        if not row:
            raise DatabaseConfigurationError(f"Missing entitlement row for user {user_id}")
        return row

    def _read_snapshot(self, conn: Any, user_id: int, now: datetime, entitlement_row: Optional[Dict[str, Any]] = None) -> EntitlementSnapshot:
        entitlement = entitlement_row or self._lock_entitlement(conn, user_id, now)
        trial_started_at = _parse_timestamp(entitlement["trial_started_at"])
        trial_ends_at = trial_started_at + timedelta(days=_trial_days())
        trial_active = now < trial_ends_at
        usage_date = _utc_date_str(now)
        daily_usage = self._fetchone(
            conn,
            "SELECT used_games FROM daily_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, usage_date),
        )
        used_games = int(daily_usage["used_games"]) if daily_usage else 0
        daily_limit = _free_games_per_day() if trial_active else 0
        daily_remaining = max(daily_limit - used_games, 0)
        paid_balance = int(entitlement["paid_games_balance"])
        total_available = daily_remaining + max(paid_balance, 0)
        return EntitlementSnapshot(
            user_id=user_id,
            trial_started_at=_isoformat(trial_started_at),
            trial_ends_at=_isoformat(trial_ends_at),
            trial_active=trial_active,
            daily_free_limit=daily_limit,
            daily_free_used=used_games,
            daily_free_remaining=daily_remaining,
            paid_games_balance=paid_balance,
            total_available_games=total_available,
            can_play=(daily_remaining > 0 or paid_balance > 0),
        )

    def assert_can_play(self, user_id: int, now: Optional[datetime] = None) -> EntitlementSnapshot:
        snapshot = self.get_entitlement_snapshot(user_id, now=now)
        if not snapshot.can_play:
            raise EntitlementError(snapshot)
        return snapshot

    def consume_game(self, user_id: int, event_key: str, source: str, now: Optional[datetime] = None) -> UsageResult:
        if not event_key:
            raise ValueError("event_key is required")

        current_time = now or utc_now()
        usage_date = _utc_date_str(current_time)
        now_iso = _isoformat(current_time)

        with self._connection(write=True) as conn:
            entitlement = self._lock_entitlement(conn, user_id, current_time)
            existing = self._fetchone(conn, "SELECT charge_kind FROM usage_events WHERE event_key = ?", (event_key,))
            if existing:
                snapshot = self._read_snapshot(conn, user_id, current_time, entitlement_row=entitlement)
                self._commit(conn)
                return UsageResult(consumed=False, charge_kind=str(existing["charge_kind"]), snapshot=snapshot)

            snapshot = self._read_snapshot(conn, user_id, current_time, entitlement_row=entitlement)
            if snapshot.trial_active and snapshot.daily_free_remaining > 0:
                self._execute(
                    conn,
                    """
                    INSERT INTO daily_usage (user_id, usage_date, used_games)
                    VALUES (?, ?, 1)
                    ON CONFLICT (user_id, usage_date) DO UPDATE SET used_games = daily_usage.used_games + 1
                    """,
                    (user_id, usage_date),
                )
                charge_kind = "free_trial"
            elif snapshot.paid_games_balance > 0:
                self._execute(
                    conn,
                    """
                    UPDATE user_entitlements
                    SET paid_games_balance = paid_games_balance - 1,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    (now_iso, user_id),
                )
                charge_kind = "paid"
            else:
                raise EntitlementError(snapshot)

            self._execute(
                conn,
                """
                INSERT INTO usage_events (event_key, user_id, source, charge_kind, usage_date, games_delta, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (event_key, user_id, source, charge_kind, usage_date, now_iso),
            )
            updated_snapshot = self._read_snapshot(conn, user_id, current_time)
            self._commit(conn)
            return UsageResult(consumed=True, charge_kind=charge_kind, snapshot=updated_snapshot)

    def apply_app_store_transaction(
        self,
        user_id: int,
        transaction_id: str,
        original_transaction_id: Optional[str],
        product_id: str,
        environment: str,
        signed_transaction_info: str,
        notification_type: Optional[str] = None,
        revoked: bool = False,
        now: Optional[datetime] = None,
    ) -> PurchaseResult:
        if not transaction_id:
            raise ValueError("transaction_id is required")

        current_time = now or utc_now()
        now_iso = _isoformat(current_time)
        games_granted = _app_store_games_pack()

        with self._connection(write=True) as conn:
            self._lock_entitlement(conn, user_id, current_time)
            existing = self._fetchone(conn, "SELECT * FROM app_store_transactions WHERE transaction_id = ?", (transaction_id,))
            if existing and int(existing["user_id"]) != user_id:
                raise ValueError("Transaction already belongs to a different user")

            applied_delta = 0
            already_processed = False
            if revoked:
                if existing and existing["status"] == "revoked":
                    already_processed = True
                else:
                    applied_delta = -games_granted
                    if existing:
                        self._execute(
                            conn,
                            """
                            UPDATE app_store_transactions
                            SET original_transaction_id = ?,
                                product_id = ?,
                                environment = ?,
                                signed_transaction_info = ?,
                                status = 'revoked',
                                games_granted = ?,
                                notification_type = ?,
                                updated_at = ?
                            WHERE transaction_id = ?
                            """,
                            (
                                original_transaction_id,
                                product_id,
                                environment,
                                signed_transaction_info,
                                games_granted,
                                notification_type,
                                now_iso,
                                transaction_id,
                            ),
                        )
                    else:
                        self._execute(
                            conn,
                            """
                            INSERT INTO app_store_transactions (
                                transaction_id,
                                original_transaction_id,
                                user_id,
                                product_id,
                                environment,
                                signed_transaction_info,
                                status,
                                games_granted,
                                notification_type,
                                created_at,
                                updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, 'revoked', ?, ?, ?, ?)
                            """,
                            (
                                transaction_id,
                                original_transaction_id,
                                user_id,
                                product_id,
                                environment,
                                signed_transaction_info,
                                games_granted,
                                notification_type,
                                now_iso,
                                now_iso,
                            ),
                        )
            else:
                if existing:
                    already_processed = True
                else:
                    applied_delta = games_granted
                    self._execute(
                        conn,
                        """
                        INSERT INTO app_store_transactions (
                            transaction_id,
                            original_transaction_id,
                            user_id,
                            product_id,
                            environment,
                            signed_transaction_info,
                            status,
                            games_granted,
                            notification_type,
                            created_at,
                            updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'credited', ?, ?, ?, ?)
                        """,
                        (
                            transaction_id,
                            original_transaction_id,
                            user_id,
                            product_id,
                            environment,
                            signed_transaction_info,
                            games_granted,
                            notification_type,
                            now_iso,
                            now_iso,
                        ),
                    )

            if applied_delta:
                self._execute(
                    conn,
                    """
                    UPDATE user_entitlements
                    SET paid_games_balance = paid_games_balance + ?,
                        updated_at = ?
                    WHERE user_id = ?
                    """,
                    (applied_delta, now_iso, user_id),
                )

            snapshot = self._read_snapshot(conn, user_id, current_time)
            self._commit(conn)
            return PurchaseResult(
                applied=(applied_delta != 0),
                revoked=revoked,
                already_processed=already_processed,
                games_changed=applied_delta,
                snapshot=snapshot,
            )
