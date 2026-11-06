"""Generic RSS/Atom connector. Most blogs and news sites are added purely via config."""

from __future__ import annotations

import feedparser

from onyourtips.core.models import RawItem, SourceKind
from onyourtips.core.utils import parse_datetime
from onyourtips.ingestion.sources.base import Source, register


def parse_feed(content: bytes | str, source_id: str, limit: int) -> list[RawItem]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"unparseable feed: {feed.get('bozo_exception')}")
    items: list[RawItem] = []
    for e in feed.entries[:limit]:
        link, title = e.get("link"), e.get("title")
        if not link or not title:
            continue
        summary = e.get("summary")
        if not summary and e.get("content"):
            summary = e.content[0].get("value")
        authors = [a.get("name") for a in e.get("authors", []) if a.get("name")]
        items.append(
            RawItem(
                source_id=source_id,
                external_id=e.get("id") or link,
                url=link,
                title=title,
                summary=summary,
                author=", ".join(authors) or e.get("author"),
                published_at=parse_datetime(e.get("published_parsed") or e.get("updated_parsed")),
                discussion_url=e.get("comments"),
                tags=[t.get("term") for t in e.get("tags", []) if t.get("term")],
            )
        )
    return items


@register
class RssSource(Source):
    kind = SourceKind.RSS

    async def fetch(self) -> list[RawItem]:
        resp = await self.http.get(self.require_param("url"))
        return parse_feed(resp.content, self.config.id, self.config.limit)
