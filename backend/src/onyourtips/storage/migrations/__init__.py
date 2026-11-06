"""Forward-only, numbered SQL migrations tracked with SQLite's ``PRAGMA user_version``.

Add a change by creating ``NNNN_description.sql`` in this folder with the next number.
Each file runs once, inside a transaction, and bumps ``user_version`` to its number.
Never edit a migration that has shipped — add a new one.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent
_NAME = re.compile(r"^(\d{4})_[\w-]+\.sql$")


def available_migrations() -> list[tuple[int, Path]]:
    found = sorted(
        (int(m.group(1)), p) for p in MIGRATIONS_DIR.iterdir() if (m := _NAME.match(p.name))
    )
    numbers = [n for n, _ in found]
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeError(f"migrations must be numbered 0001..N without gaps, found {numbers}")
    return found


def current_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations; return the numbers applied."""
    current = current_version(conn)
    applied: list[int] = []
    for number, path in available_migrations():
        if number <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
        except sqlite3.Error as exc:
            conn.rollback()
            raise RuntimeError(f"migration {path.name} failed: {exc}") from exc
        applied.append(number)
    return applied
