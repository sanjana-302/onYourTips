from datetime import timedelta

from onyourtips.core.models import Engagement, InteractionType
from onyourtips.services import build_digest
from tests.helpers import HN, NOW, W
from tests.helpers import normalize_raw as _norm


def test_build_digest_no_decay_threshold_and_dismissals(db, tagger, settings):
    rows = [
        ("https://a.com/old-great", "Huge open-weight LLM release from last week", 3000, 6),
        ("https://a.com/new-ok", "Small Rust crate for parsing logs today", 60, 0),
        ("https://a.com/low", "Barely noticed post about something new", 0, 1),
        ("https://a.com/too-old", "Ancient history post about compilers here", 5000, 9),
        ("https://a.com/meh", "Agent framework number forty two released", 900, 2),
    ]
    for url, title, pts, days_ago in rows:
        a, s = _norm(
            tagger,
            HN,
            url=url,
            title=title,
            published_at=NOW - timedelta(days=days_ago),
            engagement=Engagement(points=pts),
        )
        db.upsert(a, s, W)
    meh = db.resolve_id(_norm(tagger, HN, url="https://a.com/meh", title="x")[0].id[:8])
    db.add_interaction(meh, InteractionType.DISMISS)

    out = build_digest(db, settings, until=NOW, days=7, min_signal=3.0, min_per_type={})
    urls = [i.article.url for i in out]
    # No decay: the 6-day-old high-signal item outranks today's.
    # Low signal, older than 7 days, and dismissed items are excluded.
    assert urls == ["https://a.com/old-great", "https://a.com/new-ok"]
