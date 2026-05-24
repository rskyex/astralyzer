"""SQLite connection and migration runner.

Single source of truth for schema is the .sql files in `migrations/`. This module
is bootstrap-only: it ensures `schema_migrations` exists, then applies pending
migration files in lexicographic order. Each migration file wraps its DDL in
BEGIN/COMMIT and writes its own row into `schema_migrations` on success.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "astralyzer.db"
MIGRATIONS_DIR = ROOT / "migrations"


def db_path() -> Path:
    override = os.environ.get("ASTRALYZER_DB_PATH")
    return Path(override) if override else DEFAULT_DB_PATH


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a connection with foreign keys enabled. Caller closes."""
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def open_conn(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def _ensure_journal_mode_wal(conn: sqlite3.Connection) -> None:
    current = conn.execute("PRAGMA journal_mode").fetchone()[0]
    if str(current).lower() != "wal":
        conn.execute("PRAGMA journal_mode = WAL")


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def migrate(
    migrations_dir: Path | None = None,
    path: Path | None = None,
) -> list[str]:
    """Apply pending migrations. Returns the list of versions newly applied."""
    md = migrations_dir or MIGRATIONS_DIR
    files = sorted(md.glob("*.sql"))
    applied: list[str] = []
    with open_conn(path) as conn:
        _ensure_journal_mode_wal(conn)
        _ensure_migrations_table(conn)
        done = {row["version"] for row in conn.execute(
            "SELECT version FROM schema_migrations")}
        for f in files:
            version = f.stem
            if version in done:
                continue
            sql = f.read_text(encoding="utf-8")
            conn.executescript(sql)
            applied.append(version)
    return applied


def applied_migrations(path: Path | None = None) -> list[tuple[str, str]]:
    with open_conn(path) as conn:
        _ensure_migrations_table(conn)
        return [(r["version"], r["applied_at"])
                for r in conn.execute(
                    "SELECT version, applied_at FROM schema_migrations ORDER BY version")]


def add_coder(
    handle: str,
    name: str,
    role: str,
    is_human: bool,
    model_ref: str | None,
    path: Path | None = None,
) -> None:
    with open_conn(path) as conn:
        conn.execute(
            "INSERT INTO coders(id, name, role, is_human, model_ref) "
            "VALUES (?, ?, ?, ?, ?)",
            (handle, name, role, 1 if is_human else 0, model_ref),
        )
        conn.commit()
