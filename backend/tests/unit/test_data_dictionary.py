"""docs/data-dictionary.md must describe every table and column the migrations create."""

import re
import sqlite3
from pathlib import Path

import onyourtips.storage.migrations as migrations

ROOT = Path(__file__).resolve().parents[3]
DICTIONARY = ROOT / "docs" / "data-dictionary.md"


def _schema() -> dict[str, list[str]]:
    conn = sqlite3.connect(":memory:")
    for f in sorted(Path(migrations.__file__).parent.glob("0*.sql")):
        conn.executescript(f.read_text())
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")] for t in tables}


def _documented() -> dict[str, set[str]]:
    docs: dict[str, set[str]] = {}
    current = None
    for line in DICTIONARY.read_text(encoding="utf-8").splitlines():
        if m := re.match(r"^### (\w+)\s*$", line):
            current = m.group(1)
            docs[current] = set()
        elif current and (m := re.match(r"^\| `(\w+)` \|", line)):
            docs[current].add(m.group(1))
    return docs


def test_every_table_and_column_is_documented():
    if not DICTIONARY.exists():  # the package installed without the repo's docs
        return
    schema, docs = _schema(), _documented()
    missing_tables = sorted(set(schema) - set(docs))
    assert not missing_tables, f"add to docs/data-dictionary.md: tables {missing_tables}"
    missing = {t: sorted(set(cols) - docs[t]) for t, cols in schema.items() if set(cols) - docs[t]}
    assert not missing, f"add to docs/data-dictionary.md: columns {missing}"
    stale = sorted(set(docs) - set(schema))
    assert not stale, f"documented tables that no longer exist: {stale}"
