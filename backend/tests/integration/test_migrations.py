import sqlite3

from onyourtips.storage import Database
from onyourtips.storage.migrations import apply_migrations, available_migrations, current_version


def test_fresh_database_is_fully_migrated(tmp_path):
    db = Database(tmp_path / "a.db")
    latest = available_migrations()[-1][0]
    assert current_version(db.conn) == latest
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"articles", "sightings", "ingestion_runs", "interactions"} <= tables
    db.close()


def test_migrations_are_idempotent(tmp_path):
    path = tmp_path / "b.db"
    Database(path).close()
    db = Database(path)  # re-open: nothing pending
    assert apply_migrations(db.conn) == []
    db.close()


def test_pre_migration_database_is_adopted(tmp_path):
    """A DB created by the old inline schema (user_version 0) upgrades without errors."""
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE articles (id TEXT PRIMARY KEY, canonical_url TEXT NOT NULL UNIQUE, "
        "url TEXT NOT NULL, title TEXT NOT NULL, title_fp TEXT, summary TEXT, author TEXT, "
        "content_type TEXT NOT NULL, published_at TEXT, first_seen_at TEXT NOT NULL, "
        "last_seen_at TEXT NOT NULL, topics TEXT NOT NULL DEFAULT '[]', "
        "source_tags TEXT NOT NULL DEFAULT '[]', primary_source_id TEXT NOT NULL, "
        "signal REAL NOT NULL DEFAULT 0, extra TEXT NOT NULL DEFAULT '{}')"
    )
    conn.commit()
    conn.close()
    db = Database(path)
    assert current_version(db.conn) == available_migrations()[-1][0]
    db.close()
