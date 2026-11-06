"""dev.to public API — top articles for a tag.

params:
  tag:      e.g. ai
  top_days: top articles from the last N days (default 1)
"""

from __future__ import annotations

from onyourtips.core.models import Engagement, RawItem, SourceKind
from onyourtips.core.utils import parse_datetime
from onyourtips.ingestion.sources.base import Source, register

API = "https://dev.to/api/articles"


@register
class DevToSource(Source):
    kind = SourceKind.DEVTO

    async def fetch(self) -> list[RawItem]:
        params: dict[str, object] = {
            "per_page": self.config.limit,
            "top": int(self.params.get("top_days", 1)),
        }
        if tag := self.params.get("tag"):
            params["tag"] = tag
        data = await self.http.get_json(API, params=params)
        items: list[RawItem] = []
        for a in data if isinstance(data, list) else []:
            if not a.get("url") or not a.get("title"):
                continue
            items.append(
                self.item(
                    external_id=str(a.get("id")),
                    url=a["url"],
                    title=a["title"],
                    summary=a.get("description"),
                    author=(a.get("user") or {}).get("username"),
                    published_at=parse_datetime(a.get("published_at")),
                    discussion_url=a["url"] + "#comments",
                    engagement=Engagement(
                        points=a.get("public_reactions_count"), comments=a.get("comments_count")
                    ),
                    tags=list(a.get("tag_list") or []),
                )
            )
        return items
