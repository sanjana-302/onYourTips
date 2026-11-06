"""HTTP API tests (FastAPI TestClient). Skipped if FastAPI isn't installed."""

from datetime import timedelta

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from onyourtips.api.main import create_app  # noqa: E402
from onyourtips.core.models import Engagement  # noqa: E402
from onyourtips.core.utils import utcnow  # noqa: E402
from onyourtips.ingestion.tagging import Tagger  # noqa: E402
from onyourtips.storage import Database  # noqa: E402
from tests.helpers import CONFIG, HN, W  # noqa: E402
from tests.helpers import normalize_raw as _norm  # noqa: E402


@pytest.fixture
def client(settings, tmp_path):
    settings.frontend_dist = tmp_path / "no-ui"
    tagger = Tagger.from_yaml(CONFIG / "taxonomy.yaml")
    now = utcnow()
    with Database(settings.db_path) as db:
        for i, title in enumerate(["New LLM beats coding benchmark", "Rust 2.0 is out today"]):
            a, s = _norm(
                tagger,
                HN,
                url=f"https://a.com/{i}",
                title=title,
                published_at=now - timedelta(hours=1),
                engagement=Engagement(points=100),
            )
            db.upsert(a.model_copy(update={"first_seen_at": now, "last_seen_at": now}), s, W)
    with TestClient(create_app(settings)) as c:
        yield c


def test_health_and_root(client):
    assert client.get("/api/health").json() == {"status": "ok"}
    assert "docs" in client.get("/").json()


def test_feed_filters(client):
    items = client.get("/api/feed").json()["items"]
    assert len(items) == 2 and {"article", "sightings", "score", "state"} <= set(items[0])
    llm = client.get("/api/feed", params={"topic": "llms"}).json()["items"]
    assert [i["article"]["title"] for i in llm] == ["New LLM beats coding benchmark"]
    assert client.get("/api/feed", params={"type": "project"}).json()["items"] == []
    assert client.get("/api/feed", params={"type": "nope"}).status_code == 422


def test_article_interactions_roundtrip(client):
    aid = client.get("/api/feed").json()["items"][0]["article"]["id"]
    r = client.post(f"/api/articles/{aid[:8]}/interactions", json={"action": "save"})
    assert r.status_code == 200 and r.json()["saved"] is True
    r = client.post(f"/api/articles/{aid}/interactions", json={"action": "rate", "value": 5})
    assert r.json()["rating"] == 5
    assert client.get(f"/api/articles/{aid}").json()["state"]["saved"] is True
    assert [i["article"]["id"] for i in client.get("/api/saved").json()["items"]] == [aid]
    bad = client.post(f"/api/articles/{aid}/interactions", json={"action": "rate", "value": 9})
    assert bad.status_code == 422
    assert (
        client.post("/api/articles/ffffffff/interactions", json={"action": "save"}).status_code
        == 404
    )
    assert client.get("/api/articles/ffffffff").status_code == 404


def test_digest_topics_interests_status(client):
    d = client.get("/api/digest", params={"days": 7}).json()
    assert d["days"] == 7 and isinstance(d["items"], list)
    topics = client.get("/api/topics").json()
    assert topics[0]["count"] >= topics[-1]["count"] and "label" in topics[0]
    assert client.get("/api/interests").json() == []
    st = client.get("/api/status").json()
    assert st["articles"] == 2 and st["last_run_at"] is None


def test_ingest_status_idle(client):
    assert client.get("/api/ingest").json()["running"] is False


def test_sources(client):
    srcs = client.get("/api/sources").json()
    assert {"id", "name", "kind", "content_type", "enabled"} <= set(srcs[0])
    assert any(s["id"] == "hn-front" for s in srcs)


def test_costs_and_llm_status(client):
    c = client.get("/api/costs", params={"days": 7, "by": "purpose"}).json()
    assert c["total_calls"] == 0 and c["rows"] == [] and c["by"] == "purpose"
    assert client.get("/api/costs", params={"by": "nope"}).status_code == 422
    st = client.get("/api/llm/status").json()  # Ollama isn't running in tests
    assert st["provider"] == "ollama" and st["reachable"] in (True, False)


def test_content_404_until_extracted(client):
    aid = client.get("/api/feed").json()["items"][0]["article"]["id"]
    assert client.get(f"/api/articles/{aid}/content").status_code == 404
