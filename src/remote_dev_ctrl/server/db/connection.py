"""Database connection management."""

import sqlite3
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

from ..config import get_rdc_home

# Map legacy names → new name "rdc"
_DB_NAME_ALIASES = {"main": "rdc", "adt": "rdc"}


class DatabaseManager:
    """Manages SQLite database connections for rdc.db, tasks.db, logs.db."""

    def __init__(self, db_dir: Optional[Path] = None):
        self.db_dir = db_dir or get_rdc_home() / "data"
        self.db_dir.mkdir(parents=True, exist_ok=True)
        self._connections: dict[str, sqlite3.Connection] = {}

    def get_connection(self, db_name: str) -> sqlite3.Connection:
        """Get or create a connection to a database."""
        # Resolve aliases (e.g. "main" → "rdc", "adt" → "rdc")
        db_name = _DB_NAME_ALIASES.get(db_name, db_name)

        if db_name not in self._connections:
            db_path = self.db_dir / f"{db_name}.db"
            # Compat: if rdc.db doesn't exist but adt.db does, use adt.db
            if db_name == "rdc" and not db_path.exists():
                legacy_path = self.db_dir / "adt.db"
                if legacy_path.exists():
                    db_path = legacy_path
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            self._connections[db_name] = conn
        return self._connections[db_name]

    @contextmanager
    def transaction(self, db_name: str):
        """Context manager for database transactions."""
        db_name = _DB_NAME_ALIASES.get(db_name, db_name)
        conn = self.get_connection(db_name)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def close_all(self):
        """Close all database connections."""
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()


# Global database manager
_db_manager: Optional[DatabaseManager] = None


def get_db_manager() -> DatabaseManager:
    """Get the global database manager."""
    global _db_manager
    if _db_manager is None:
        _db_manager = DatabaseManager()
    return _db_manager


def get_db(db_name: str = "rdc") -> sqlite3.Connection:
    """Get a database connection by name.

    Valid names: "rdc" (or "adt"/"main" for backward compat), "tasks", "logs".
    """
    return get_db_manager().get_connection(db_name)


def init_databases():
    """Initialize all database schemas via dbmate migrations."""
    from .migrate import ensure_database
    ensure_database()


def close_databases():
    """Close all database connections."""
    global _db_manager
    if _db_manager:
        _db_manager.close_all()
        _db_manager = None
