from datetime import timedelta

from onyourtips.core.models import ContentType, Engagement, UpsertOutcome
from tests.helpers import GH, HN, NOW, RSS, W
from tests.helpers import normalize_raw as _norm


def test_new_then_updated(db, tagger):
    a, s = _norm(
        tagger,
        HN,
        url="https://example.com/x",
        title="An LLM story",
        published_at=NOW,
        engagement=Engagement(points=10),
    )
    assert db.upsert(a, s, W) is UpsertOutcome.NEW
    a2, s2 = _norm(
        tagger,
        HN,
        url="https://example.com/x?utm_source=hn",
        title="An LLM story",
        published_at=NOW,
        engagement=Engagement(points=500),
    )
    assert db.upsert(a2, s2, W) is UpsertOutcome.UPDATED
    [sight] = db.get_sightings(a.id)
    assert sight.engagement.points == 500


def test_same_url_different_source_merges(db, tagger):
    a, s = _norm(
        tagger,
        HN,
        url="https://github.com/Foo/Bar",
        title="Bar: a fast vector database",
        published_at=NOW,
        engagement=Engagement(points=100),
    )
    db.upsert(a, s, W)
    signal_one = db.get_article(a.id).signal
    a2, s2 = _norm(
        tagger,
        GH,
        url="https://github.com/foo/bar",
        title="foo/bar",
        published_at=NOW - timedelta(days=2),
        engagement=Engagement(stars=900),
        tags=["rust"],
    )
    assert db.upsert(a2, s2, W) is UpsertOutcome.MERGED
    merged = db.get_article(a.id)
    assert merged.content_type == ContentType.PROJECT
    assert merged.published_at == NOW - timedelta(days=2)
    assert "rust" in merged.source_tags
    assert merged.signal > signal_one
    assert {s.source_id for s in db.get_sightings(a.id)} == {"hn", "gh"}


def test_title_fingerprint_merges_different_urls(db, tagger):
    a, s = _norm(
        tagger,
        RSS,
        url="https://lab.ai/blog/model-x",
        title="Introducing Model X: our best model yet",
        published_at=NOW,
    )
    db.upsert(a, s, W)
    a2, s2 = _norm(
        tagger,
        HN,
        url="https://news.site/model-x-launch",
        title="Introducing Model X — our best model yet",
        published_at=NOW,
    )
    assert db.upsert(a2, s2, W) is UpsertOutcome.MERGED
    assert db.get_article(a2.id) is None


def test_title_fingerprint_respects_window(db, tagger):
    a, s = _norm(
        tagger,
        RSS,
        url="https://a.com/1",
        title="Weekly roundup of the best tools",
        published_at=NOW - timedelta(days=30),
    )
    db.upsert(a, s, W)
    a2, s2 = _norm(
        tagger,
        RSS,
        url="https://a.com/2",
        title="Weekly roundup of the best tools",
        published_at=NOW,
    )
    assert db.upsert(a2, s2, W) is UpsertOutcome.NEW


def test_feed_filters_and_ranking(db, tagger):
    rows = [
        (HN, "https://a.com/llm", "New LLM beats benchmark", 800, NOW - timedelta(hours=2)),
        (HN, "https://a.com/rust", "Rust 2.0 is out today", 50, NOW - timedelta(hours=1)),
        (HN, "https://a.com/old", "Old LLM news from last week", 2000, NOW - timedelta(days=5)),
    ]
    for cfg, url, title, pts, when in rows:
        a, s = _norm(
            tagger, cfg, url=url, title=title, published_at=when, engagement=Engagement(points=pts)
        )
        db.upsert(a, s, W)

    feed = db.feed(since=NOW - timedelta(hours=24), now=NOW)
    assert [i.article.url for i in feed] == ["https://a.com/llm", "https://a.com/rust"]
    assert feed[0].sightings[0].source_id == "hn"

    llm = db.feed(since=NOW - timedelta(days=7), topics=["llms"], now=NOW)
    assert {i.article.url for i in llm} == {"https://a.com/llm", "https://a.com/old"}

    assert (
        db.feed(since=NOW - timedelta(days=7), content_types=[ContentType.PROJECT], now=NOW) == []
    )
    assert len(db.feed(since=NOW - timedelta(days=7), query="rust", now=NOW)) == 1


def test_normalize_rejects_bad_items(tagger):
    assert _norm(tagger, HN, url="ftp://x.com/a", title="x") is None
    assert _norm(tagger, HN, url="https://x.com/a", title="   ") is None


def test_normalize_drops_future_dates(tagger):
    a, _ = _norm(
        tagger, HN, url="https://x.com/a", title="Future", published_at=NOW + timedelta(days=1)
    )
    assert a.published_at is None
