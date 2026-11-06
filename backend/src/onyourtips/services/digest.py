"""Use-case functions shared by the CLI and (later) the FastAPI layer."""

from __future__ import annotations

from datetime import datetime, timedelta

from onyourtips.core.models import ContentType, FeedItem
from onyourtips.core.settings import Settings
from onyourtips.core.utils import utcnow
from onyourtips.ranking.digest import DigestConfig, select_digest
from onyourtips.storage import Database


def build_digest(
    db: Database,
    settings: Settings,
    *,
    days: int | None = None,
    until: datetime | None = None,
    limit: int | None = None,
    min_signal: float | None = None,
    max_per_topic: int | None = None,
    min_per_type: dict[ContentType, int] | None = None,
    topics: list[str] | None = None,
) -> list[FeedItem]:
    """Best-of-window list: no decay, quality threshold, topic diversity, type quotas."""
    until = until or utcnow()
    since = until - timedelta(days=days or settings.digest_days)
    candidates = db.feed(
        since=since,
        until=until,
        topics=topics,
        limit=5000,
        half_life_hours=None,
        min_signal=settings.digest_min_signal if min_signal is None else min_signal,
    )
    cfg = DigestConfig(
        limit=limit or settings.digest_limit,
        max_per_topic=max_per_topic or settings.digest_max_per_topic,
        min_per_type=(
            min_per_type
            if min_per_type is not None
            else {ContentType(k): v for k, v in settings.digest_min_per_type.items()}
        ),
    )
    return select_digest(candidates, cfg)
