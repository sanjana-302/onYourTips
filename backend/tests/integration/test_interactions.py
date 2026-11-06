from datetime import timedelta

import pytest

from onyourtips.core.models import InteractionType as I
from tests.helpers import HN, NOW, W
from tests.helpers import normalize_raw as _norm


@pytest.fixture
def ids(db, tagger):
    out = []
    for url, title in [
        ("https://a.com/1", "New LLM coding agent release"),
        ("https://a.com/2", "Rust 2.0 is finally out now"),
        ("https://a.com/3", "Kubernetes on AWS deep dive"),
    ]:
        a, s = _norm(tagger, HN, url=url, title=title, published_at=NOW - timedelta(hours=1))
        db.upsert(a, s, W)
        out.append(a.id)
    return out


def test_state_folds_event_log(db, ids):
    a = ids[0]
    db.add_interaction(a, I.OPEN, at=NOW)
    db.add_interaction(a, I.SAVE, at=NOW + timedelta(minutes=1))
    db.add_interaction(a, I.RATE, value=2, at=NOW + timedelta(minutes=2))
    db.add_interaction(a, I.RATE, value=5, at=NOW + timedelta(minutes=3))
    st = db.get_states([a])[a]
    assert st.opened and st.saved and st.rating == 5 and not st.dismissed
    db.add_interaction(a, I.UNSAVE)
    assert db.get_states([a])[a].saved is False
    assert len(db.get_interactions(article_id=a)) == 5  # append-only


def test_validation(db, ids):
    with pytest.raises(ValueError):
        db.add_interaction(ids[0], I.RATE, value=7)
    with pytest.raises(KeyError):
        db.add_interaction("deadbeefdeadbeef", I.SAVE)


def test_resolve_id(db, ids):
    assert db.resolve_id(ids[0][:8]) == ids[0]
    assert db.resolve_id(ids[0].upper()) == ids[0]
    with pytest.raises(KeyError):
        db.resolve_id("zzzzzzzz")
    with pytest.raises(ValueError):
        db.resolve_id("ab")


def test_dismiss_hides_from_feed_and_undismiss_restores(db, ids):
    since = NOW - timedelta(days=1)
    assert len(db.feed(since=since, now=NOW)) == 3
    db.add_interaction(ids[1], I.DISMISS)
    assert ids[1] not in {i.article.id for i in db.feed(since=since, now=NOW)}
    assert len(db.feed(since=since, now=NOW, include_dismissed=True)) == 3
    db.add_interaction(ids[1], I.UNDISMISS)
    assert len(db.feed(since=since, now=NOW)) == 3


def test_feed_items_carry_state(db, ids):
    db.add_interaction(ids[2], I.SAVE)
    by_id = {i.article.id: i for i in db.feed(since=NOW - timedelta(days=1), now=NOW)}
    assert by_id[ids[2]].state.saved and not by_id[ids[0]].state.saved


def test_saved_list_order_and_unsave(db, ids):
    db.add_interaction(ids[0], I.SAVE, at=NOW)
    db.add_interaction(ids[1], I.SAVE, at=NOW + timedelta(hours=1))
    db.add_interaction(ids[2], I.SAVE, at=NOW + timedelta(hours=2))
    db.add_interaction(ids[2], I.UNSAVE, at=NOW + timedelta(hours=3))
    assert [i.article.id for i in db.saved()] == [ids[1], ids[0]]
    assert [i.article.id for i in db.saved(since=NOW + timedelta(minutes=30))] == [ids[1]]
    assert db.stats()["saved"] == 2


def test_interest_profile(db, ids):
    db.add_interaction(ids[0], I.SAVE, at=NOW)  # llms, agents, ai-coding: +3
    db.add_interaction(ids[0], I.OPEN, at=NOW)  # +1
    db.add_interaction(ids[1], I.OPEN, at=NOW)  # programming-languages: +1
    db.add_interaction(ids[2], I.DISMISS, at=NOW)  # cloud-infra: -2
    profile = db.interest_profile(since=NOW - timedelta(days=7))
    assert profile["llms"] == 4 and profile["programming-languages"] == 1
    assert profile["cloud-infra"] == -2
    assert list(profile)[-1] == "cloud-infra"
