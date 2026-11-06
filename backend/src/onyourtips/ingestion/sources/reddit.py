"""Reddit subreddit top posts. Tries the JSON listing first (has scores), falls back to RSS
(no scores) when Reddit blocks unauthenticated JSON requests.

params:
  subreddit: e.g. MachineLearning
  time:      hour | day | week   (default day)
"""

from __future__ import annotations

import logging

import httpx

from onyourtips.core.models import Engagement, RawItem, SourceKind
from onyourtips.core.utils import parse_datetime
from onyourtips.ingestion.sources.base import Source, register
from onyourtips.ingestion.sources.rss import parse_feed

log = logging.getLogger(__name__)
BASE = "https://www.reddit.com"


@register
class RedditSource(Source):
    kind = SourceKind.REDDIT

    async def fetch(self) -> list[RawItem]:
        sub = self.require_param("subreddit")
        t = self.params.get("time", "day")
        try:
            return await self._fetch_json(sub, t)
        except (httpx.HTTPStatusError, ValueError) as exc:
            log.info("reddit JSON failed for r/%s (%s); falling back to RSS", sub, exc)
            resp = await self.http.get(
                f"{BASE}/r/{sub}/top/.rss", params={"t": t, "limit": self.config.limit}
            )
            items = parse_feed(resp.content, self.config.id, self.config.limit)
            for it in items:  # RSS link is the comments page
                it.discussion_url = it.url
            return items

    async def _fetch_json(self, sub: str, t: str) -> list[RawItem]:
        data = await self.http.get_json(
            f"{BASE}/r/{sub}/top.json", params={"t": t, "limit": self.config.limit, "raw_json": 1}
        )
        items: list[RawItem] = []
        for child in data.get("data", {}).get("children", []):
            p = child.get("data") or {}
            if p.get("stickied") or p.get("over_18") or not p.get("title"):
                continue
            permalink = BASE + p.get("permalink", "")
            url = (
                permalink
                if p.get("is_self")
                else (p.get("url_overridden_by_dest") or p.get("url") or permalink)
            )
            items.append(
                self.item(
                    external_id=p.get("id"),
                    url=url,
                    title=p["title"],
                    summary=p.get("selftext") or None,
                    author=p.get("author"),
                    published_at=parse_datetime(p.get("created_utc")),
                    discussion_url=permalink,
                    engagement=Engagement(points=p.get("score"), comments=p.get("num_comments")),
                    tags=[p["link_flair_text"]] if p.get("link_flair_text") else [],
                )
            )
        return items
