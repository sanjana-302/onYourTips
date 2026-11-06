from onyourtips.core.models import Article, ContentType, FeedItem
from onyourtips.ranking.digest import DigestConfig, primary_topics, select_digest
from tests.helpers import NOW


def item(aid: str, score: float, topics: list[str], ctype=ContentType.NEWS) -> FeedItem:
    art = Article(
        id=aid,
        canonical_url=f"https://x.com/{aid}",
        url=f"https://x.com/{aid}",
        title=aid,
        content_type=ctype,
        first_seen_at=NOW,
        last_seen_at=NOW,
        topics=topics,
        primary_source_id="s",
        signal=score,
    )
    return FeedItem(article=art, sightings=[], score=score)


def test_primary_topic_is_rarest():
    items = [item("a", 1, ["llms", "ai-coding"]), item("b", 1, ["llms"]), item("c", 1, [])]
    p = primary_topics(items)
    assert p == {"a": "ai-coding", "b": "llms", "c": "other"}


def test_topic_cap_enforces_diversity():
    pool = [item(f"llm{i}", 100 - i, ["llms"]) for i in range(10)] + [item("rust", 1, ["rust"])]
    out = select_digest(pool, DigestConfig(limit=4, max_per_topic=3))
    assert [i.article.id for i in out] == ["llm0", "llm1", "llm2", "rust"]


def test_caps_relax_when_short():
    pool = [item(f"llm{i}", 100 - i, ["llms"]) for i in range(5)]
    out = select_digest(pool, DigestConfig(limit=5, max_per_topic=2))
    assert len(out) == 5


def test_type_quota_beats_higher_scores():
    pool = [item(f"news{i}", 100 - i, [f"t{i}"]) for i in range(10)]
    pool += [
        item("proj", 1, ["tp"], ContentType.PROJECT),
        item("paper", 0.5, ["tq"], ContentType.RESEARCH_PAPER),
    ]
    out = select_digest(
        pool,
        DigestConfig(
            limit=4,
            max_per_topic=5,
            min_per_type={ContentType.PROJECT: 1, ContentType.RESEARCH_PAPER: 1},
        ),
    )
    ids = [i.article.id for i in out]
    assert ids == ["news0", "news1", "proj", "paper"]  # sorted by score


def test_selection_is_deterministic():
    pool = [item(f"x{i}", 5, ["a"]) for i in range(8)]
    cfg = DigestConfig(limit=3, max_per_topic=5)
    assert [i.article.id for i in select_digest(pool, cfg)] == [
        i.article.id for i in select_digest(list(reversed(pool)), cfg)
    ]
