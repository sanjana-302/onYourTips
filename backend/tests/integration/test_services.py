from datetime import timedelta

import pytest

from onyourtips import services
from onyourtips.core.models import Engagement
from onyourtips.core.models import InteractionType as I
from onyourtips.core.utils import utcnow
from tests.helpers import GH, HN, W
from tests.helpers import normalize_raw as _norm


@pytest.fixture
def seeded(db, tagger):
    now = utcnow()
    rows = [
        (HN, "https://a.com/llm", "New open-weight LLM tops coding benchmark", 500),
        (GH, "https://github.com/acme/agentkit", "acme/agentkit", 0),
        (HN, "https://a.com/rust", "Rust 2.0 released with async traits", 80),
    ]
    ids = []
    for cfg, url, title, pts in rows:
        a, s = _norm(
            tagger,
            cfg,
            url=url,
            title=title,
            published_at=now - timedelta(hours=2),
            engagement=Engagement(points=pts),
            tags=["agents"] if cfg is GH else [],
        )
        # normalize_raw stamps NOW (fixed); restamp to real time so feed windows include it
        a = a.model_copy(update={"first_seen_at": now, "last_seen_at": now})
        db.upsert(a, s, W)
        ids.append(a.id)
    return ids


def test_get_article_by_prefix(db, seeded):
    item = services.get_article(db, seeded[0][:8])
    assert item and item.article.id == seeded[0] and item.sightings[0].source_id == "hn"
    assert services.get_article(db, "ffffffff") is None
    assert services.get_article(db, "ab") is None  # too short → None, not an exception


def test_record_interaction_returns_state(db, seeded):
    st = services.record_interaction(db, seeded[0][:8], I.SAVE)
    assert st.saved
    st = services.record_interaction(db, seeded[0], I.RATE, value=4)
    assert st.saved and st.rating == 4
    with pytest.raises(KeyError):
        services.record_interaction(db, "ffffffff", I.SAVE)


def test_feed_and_saved(db, settings, seeded):
    assert len(services.get_feed(db, settings, hours=24)) == 3
    services.record_interaction(db, seeded[2], I.DISMISS)
    assert len(services.get_feed(db, settings, hours=24)) == 2
    services.record_interaction(db, seeded[1], I.SAVE)
    assert [i.article.id for i in services.list_saved(db)] == [seeded[1]]


def test_topics_counts_and_order(db, tagger, seeded):
    topics = services.list_topics(db, tagger)
    by_slug = {t.slug: t for t in topics}
    assert by_slug["llms"].count == 1 and by_slug["agents"].count >= 1
    assert topics[0].count >= topics[-1].count
    assert by_slug["llms"].label == "LLMs & Foundation Models"


def test_interests_have_labels(db, tagger, seeded):
    services.record_interaction(db, seeded[0], I.SAVE)
    out = services.interests(db, tagger)
    assert out[0].slug == "llms" or "llms" in [t.slug for t in out]
    assert all(t.label for t in out)


def test_app_status(db, seeded):
    st = services.app_status(db)
    assert st.articles == 3 and st.saved == 0 and st.last_run_at is None and st.sources == []


def test_ingest_runner_single_flight(settings, monkeypatch):
    import threading

    gate = threading.Event()
    runner = services.IngestRunner(settings)

    def fake_ingest():
        gate.wait(5)
        raise RuntimeError("boom")

    monkeypatch.setattr(runner, "_ingest", fake_ingest)
    assert runner.start() is True
    assert runner.start() is False  # already running
    assert runner.status.running
    gate.set()
    runner.wait(5)
    st = runner.status
    assert not st.running and st.error == "RuntimeError: boom" and st.finished_at


def test_list_sources(settings):
    srcs = services.list_sources(settings)
    assert len(srcs) >= 10 and srcs[0].name
