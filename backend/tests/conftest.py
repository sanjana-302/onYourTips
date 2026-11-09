from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from onyourtips.core.http import HttpClient
from onyourtips.core.settings import Settings
from onyourtips.ingestion.tagging import Tagger
from onyourtips.storage import Database
from tests.helpers import CONFIG


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        sources_file=CONFIG / "sources.yaml",
        taxonomy_file=CONFIG / "taxonomy.yaml",
        http_retries=1,
        http_backoff=0,
        max_age_days=36500,
        summary_nightly=False,  # nightly tests opt in, with a fake model
        _env_file=None,
    )


@pytest.fixture
def tagger() -> Tagger:
    return Tagger.from_yaml(CONFIG / "taxonomy.yaml")


@pytest.fixture
def db(tmp_path: Path):
    database = Database(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield HttpClient(client, retries=1, backoff=0)
