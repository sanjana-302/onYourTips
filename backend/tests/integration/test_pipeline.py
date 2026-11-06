"""End-to-end pipeline run with mocked sources."""

from datetime import timedelta

import respx

from onyourtips.core.models import SourceConfig
from onyourtips.ingestion.pipeline import Pipeline
from tests.helpers import FIXTURES, NOW

SOURCES = [
    SourceConfig(
        id="blog",
        name="Blog",
        kind="rss",
        content_type="blog_post",
        weight=2,
        params={"url": "https://example.com/feed"},
    ),
    SourceConfig(id="hn", name="HN", kind="hackernews", content_type="news"),
    SourceConfig(
        id="broken",
        name="Broken",
        kind="rss",
        content_type="news",
        params={"url": "https://broken.example/feed"},
    ),
    SourceConfig(
        id="off",
        name="Disabled",
        kind="rss",
        content_type="news",
        enabled=False,
        params={"url": "https://off.example/feed"},
    ),
]


@respx.mock
async def test_pipeline_run(settings, tagger, db, http):
    respx.get("https://example.com/feed").respond(
        content=(FIXTURES / "sample_feed.xml").read_bytes()
    )
    respx.get("https://broken.example/feed").respond(status_code=404)
    # HN links to the same blog post (with different tracking params) -> should merge.
    respx.get("https://hn.algolia.com/api/v1/search").respond(
        json={
            "hits": [
                {
                    "objectID": "77",
                    "title": "Introducing a new open-weight LLM for coding agents",
                    "url": "https://example.com/blog/new-llm?utm_source=hn",
                    "points": 400,
                    "num_comments": 120,
                    "created_at_i": int((NOW - timedelta(hours=3)).timestamp()),
                },
            ]
        }
    )

    report = await Pipeline(settings, SOURCES, tagger, db).run(http=http)
    by_id = {r.source_id: r for r in report.results}

    assert "off" not in by_id
    assert by_id["broken"].ok is False and "404" in by_id["broken"].error
    assert by_id["blog"].new == 2
    assert by_id["hn"].merged == 1 and by_id["hn"].new == 0

    st = db.stats()
    assert st["articles"] == 2 and st["sightings"] == 3
    assert len(st["last_run"]) == 3

    feed = db.feed(since=NOW - timedelta(days=3650), limit=10)
    top = feed[0].article
    assert top.canonical_url == "https://example.com/blog/new-llm"
    assert {"llms", "agents"} <= set(top.topics)
    assert {s.source_id for s in feed[0].sightings} == {"blog", "hn"}

    # Re-running is idempotent: nothing new, sightings refreshed.
    report2 = await Pipeline(settings, SOURCES, tagger, db).run(only={"blog"}, http=http)
    assert report2.results[0].new == 0 and report2.results[0].updated == 2
    assert db.stats()["articles"] == 2
