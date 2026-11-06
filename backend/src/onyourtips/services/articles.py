"""Use cases behind the feed, article detail, interactions and the "You" screen."""

from __future__ import annotations

from datetime import datetime, timedelta

from pydantic import BaseModel

from onyourtips.core.models import ArticleState, ContentType, FeedItem, InteractionType
from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.ingestion.tagging import Tagger
from onyourtips.storage import Database


class TopicInfo(BaseModel):
    slug: str
    label: str
    count: int = 0  # articles carrying this topic in the window (0 when not computed)


class TopicInterest(BaseModel):
    slug: str
    label: str
    score: float


class SourceHealth(BaseModel):
    source_id: str
    ok: bool
    fetched: int
    new: int
    error: str | None = None


class SourceInfo(BaseModel):
    id: str
    name: str
    kind: str
    content_type: ContentType
    enabled: bool


def list_sources(settings: Settings) -> list[SourceInfo]:
    from onyourtips.ingestion.sources.base import load_source_configs

    return [
        SourceInfo(
            id=c.id, name=c.name, kind=c.kind.value, content_type=c.content_type, enabled=c.enabled
        )
        for c in load_source_configs(settings.sources_file)
    ]


class AppStatus(BaseModel):
    articles: int
    saved: int
    interactions: int
    last_run_at: str | None
    sources: list[SourceHealth]
    # Daily refresh (ADR 0005)
    last_refresh_at: datetime | None = None
    last_refresh_trigger: str | None = None  # scheduled | manual
    last_refresh_status: str | None = None  # ok | partial | failed | running
    next_refresh_at: datetime | None = None
    open_alerts: int = 0


def get_feed(
    db: Database,
    settings: Settings,
    *,
    hours: int = 24,
    topics: list[str] | None = None,
    content_types: list[ContentType] | None = None,
    query: str | None = None,
    limit: int = 30,
) -> list[FeedItem]:
    return db.feed(
        since=utcnow() - timedelta(hours=hours),
        topics=topics or None,
        content_types=content_types or None,
        query=query or None,
        limit=limit,
        half_life_hours=settings.half_life_hours,
    )


def get_article(db: Database, article_id: str) -> FeedItem | None:
    """Full article with sightings and your state. Accepts a unique id prefix."""
    try:
        full_id = db.resolve_id(article_id)
    except (KeyError, ValueError):
        return None
    art = db.get_article(full_id)
    if art is None:
        return None
    return FeedItem(
        article=art,
        sightings=db.get_sightings(full_id),
        score=art.signal,
        state=db.get_states([full_id]).get(full_id, ArticleState()),
    )


def record_interaction(
    db: Database,
    article_id: str,
    action: InteractionType,
    value: int | None = None,
    note: str | None = None,
) -> ArticleState:
    """Append an interaction and return the article's new state. Raises KeyError / ValueError."""
    full_id = db.resolve_id(article_id)
    db.add_interaction(full_id, action, value=value, note=note)
    return db.get_states([full_id]).get(full_id, ArticleState())


def list_saved(db: Database, days: int | None = None) -> list[FeedItem]:
    return db.saved(since=utcnow() - timedelta(days=days) if days else None)


def list_topics(db: Database, tagger: Tagger, days: int = 7) -> list[TopicInfo]:
    """Taxonomy topics with article counts in the window, most common first (unused ones last)."""
    counts = db.topic_counts(since=utcnow() - timedelta(days=days))
    topics = [
        TopicInfo(slug=t.slug, label=t.label, count=counts.get(t.slug, 0)) for t in tagger.topics
    ]
    return sorted(topics, key=lambda t: (-t.count, t.label))


def interests(db: Database, tagger: Tagger, days: int = 7) -> list[TopicInterest]:
    labels = {t.slug: t.label for t in tagger.topics}
    profile = db.interest_profile(since=utcnow() - timedelta(days=days))
    return [TopicInterest(slug=s, label=labels.get(s, s), score=v) for s, v in profile.items()]


def app_status(db: Database, settings: Settings | None = None) -> AppStatus:
    from onyourtips.services.nightly import next_refresh_at

    st = db.stats()
    runs = st["last_run"]
    last = db.last_pipeline_run()
    return AppStatus(
        last_refresh_at=(last.finished_at or last.started_at) if last else None,
        last_refresh_trigger=last.trigger if last else None,
        last_refresh_status=last.status if last else None,
        next_refresh_at=next_refresh_at(settings) if settings else None,
        open_alerts=len(db.list_alerts()),
        articles=st["articles"],
        saved=st["saved"],
        interactions=st["interactions"],
        last_run_at=runs[0]["finished_at"] if runs else None,
        sources=[
            SourceHealth(
                source_id=r["source_id"],
                ok=bool(r["ok"]),
                fetched=r["fetched"],
                new=r["new"],
                error=r["error"],
            )
            for r in runs
        ],
    )
