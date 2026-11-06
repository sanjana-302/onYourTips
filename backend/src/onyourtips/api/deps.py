"""FastAPI dependencies."""

from collections.abc import Iterator
from functools import lru_cache

from fastapi import Request

from onyourtips.core.settings import Settings
from onyourtips.ingestion.tagging import Tagger
from onyourtips.services import IngestRunner
from onyourtips.storage import Database


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_db(request: Request) -> Iterator[Database]:
    """One SQLite connection per request (cheap, and avoids sharing across threads)."""
    db = Database(request.app.state.settings.db_path)
    try:
        yield db
    finally:
        db.close()


@lru_cache(maxsize=4)
def _tagger(path: str) -> Tagger:
    from pathlib import Path

    return Tagger.from_yaml(Path(path))


def get_tagger(request: Request) -> Tagger:
    return _tagger(str(request.app.state.settings.taxonomy_file))


def get_ingest_runner(request: Request) -> IngestRunner:
    return request.app.state.ingest
