"""
Database connection & session manager supporting PostgreSQL (via SQLAlchemy / psycopg2)
with automatic local SQLite fallback when running outside Docker.
"""

import os
import sqlite3
import sys
from typing import Any, Dict, Generator, List, Optional

from backend.config import ALLOW_SQLITE_FALLBACK, DATABASE_URL, SQLITE_FALLBACK_PATH


class DatabaseConnection:
    """
    Unified relational database connection interface executing parameterized SQL
    against PostgreSQL (when reachable via DATABASE_URL) or the local SQLite replica.
    """

    def __init__(self) -> None:
        self.engine_type = "sqlite_replica"
        self.pg_conn = None
        self.sqlite_conn: Optional[sqlite3.Connection] = None
        self._connect()

    def _connect(self) -> None:
        # Attempt PostgreSQL connection first if psycopg2 is available and DATABASE_URL is postgres
        if DATABASE_URL.startswith("postgres"):
            try:
                import psycopg2

                # 10s covers TLS + the first connect to a managed instance, which can
                # be slow right after it starts. A shorter timeout turns a cold
                # database into a permanent silent fallback.
                self.pg_conn = psycopg2.connect(
                    DATABASE_URL,
                    connect_timeout=10,
                    application_name="windward-api",
                )
                self.engine_type = "postgresql"
                return
            except Exception as exc:
                self.pg_conn = None
                reason = f"{type(exc).__name__}: {exc}".strip()
                if not ALLOW_SQLITE_FALLBACK:
                    # Fail loudly: a service that silently writes to ephemeral SQLite
                    # looks healthy while losing every write on restart.
                    raise RuntimeError(
                        "PostgreSQL connection failed and ALLOW_SQLITE_FALLBACK is disabled. "
                        f"Check DATABASE_URL. {reason}"
                    ) from exc
                sys.stderr.write(
                    "[database] WARNING: PostgreSQL unreachable, falling back to the local "
                    f"SQLite replica ({SQLITE_FALLBACK_PATH}). {reason}\n"
                )
                sys.stderr.flush()

        os.makedirs(os.path.dirname(SQLITE_FALLBACK_PATH), exist_ok=True)
        self.sqlite_conn = sqlite3.connect(SQLITE_FALLBACK_PATH, check_same_thread=False)
        self.sqlite_conn.row_factory = sqlite3.Row
        self.engine_type = "postgresql_compatible_sqlite"

    def execute_query(self, sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
        if self.pg_conn is not None:
            import psycopg2.extras

            pg_sql = sql.replace("?", "%s")
            with self.pg_conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(pg_sql, params)
                if cur.description:
                    return [dict(r) for r in cur.fetchall()]
                self.pg_conn.commit()
                return []

        assert self.sqlite_conn is not None
        cur = self.sqlite_conn.cursor()
        cur.execute(sql, params)
        if cur.description:
            rows = cur.fetchall()
            return [dict(r) for r in rows]
        self.sqlite_conn.commit()
        return []

    def close(self) -> None:
        if self.pg_conn is not None:
            self.pg_conn.close()
        if self.sqlite_conn is not None:
            self.sqlite_conn.close()


def get_db() -> Generator[DatabaseConnection, None, None]:
    """
    FastAPI Dependency for injecting a database connection into route handlers.
    """
    db = DatabaseConnection()
    try:
        yield db
    finally:
        db.close()
