"""arXiv export API — newest submissions in the given categories.

params:
  categories: list of arXiv categories, e.g. [cs.AI, cs.LG, cs.CL]
"""

from __future__ import annotations

import feedparser

from onyourtips.core.models import RawItem, SourceKind
from onyourtips.core.utils import parse_datetime
from onyourtips.ingestion.normalize import clean_text
from onyourtips.ingestion.sources.base import Source, register

API = "https://export.arxiv.org/api/query"


@register
class ArxivSource(Source):
    kind = SourceKind.ARXIV

    async def fetch(self) -> list[RawItem]:
        cats = self.params.get("categories") or ["cs.AI"]
        params = {
            "search_query": " OR ".join(f"cat:{c}" for c in cats),
            "sortBy": "submittedDate",
            "sortOrder": "descending",
            "max_results": self.config.limit,
        }
        resp = await self.http.get(API, params=params)
        feed = feedparser.parse(resp.content)

        items: list[RawItem] = []
        for e in feed.entries:
            entry_id = e.get("id", "")
            link = e.get("link") or entry_id
            if not link or not e.get("title"):
                continue
            authors = [a.get("name") for a in e.get("authors", []) if a.get("name")]
            author = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
            categories = [t.get("term") for t in e.get("tags", []) if t.get("term")]
            items.append(
                self.item(
                    external_id=entry_id.rsplit("/abs/", 1)[-1] or None,
                    url=link,
                    title=clean_text(e.title),
                    summary=e.get("summary"),
                    author=author or None,
                    published_at=parse_datetime(e.get("published_parsed")),
                    tags=categories,
                    extra={"arxiv_categories": categories},
                )
            )
        return items
