"""Hacker News via the Algolia search API (no key required).

params:
  tags:         front_page | show_hn | ask_hn | story   (default front_page)
  endpoint:     search (relevance/popularity) | search_by_date (newest first)   (default search)
  min_points:   only stories with at least this many points
  window_hours: only stories created in the last N hours. With the daily refresh this gives
                "the best of the last 24 hours" instead of "whatever is on the front page at 1 AM".
"""

from __future__ import annotations

from datetime import timedelta

from onyourtips.core.models import Engagement, RawItem, SourceKind
from onyourtips.core.utils import parse_datetime, utcnow
from onyourtips.ingestion.sources.base import Source, register

API = "https://hn.algolia.com/api/v1/"


@register
class HackerNewsSource(Source):
    kind = SourceKind.HACKERNEWS

    async def fetch(self) -> list[RawItem]:
        endpoint = self.params.get("endpoint", "search")
        params: dict[str, object] = {
            "tags": self.params.get("tags", "front_page"),
            "hitsPerPage": self.config.limit,
        }
        filters = []
        if min_points := self.params.get("min_points"):
            filters.append(f"points>={int(min_points)}")
        if window := self.params.get("window_hours"):
            since = utcnow() - timedelta(hours=int(window))
            filters.append(f"created_at_i>{int(since.timestamp())}")
        if filters:
            params["numericFilters"] = ",".join(filters)
        data = await self.http.get_json(API + endpoint, params=params)

        items: list[RawItem] = []
        for hit in data.get("hits", []):
            oid, title = hit.get("objectID"), hit.get("title")
            if not oid or not title:
                continue
            discussion = f"https://news.ycombinator.com/item?id={oid}"
            items.append(
                self.item(
                    external_id=str(oid),
                    url=hit.get("url") or discussion,  # Ask HN etc. have no external URL
                    title=title,
                    summary=hit.get("story_text"),
                    author=hit.get("author"),
                    published_at=parse_datetime(hit.get("created_at_i") or hit.get("created_at")),
                    discussion_url=discussion,
                    engagement=Engagement(
                        points=hit.get("points"), comments=hit.get("num_comments")
                    ),
                    tags=[t for t in hit.get("_tags", []) if t in ("show_hn", "ask_hn")],
                )
            )
        return items
