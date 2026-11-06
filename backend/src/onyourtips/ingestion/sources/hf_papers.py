"""Hugging Face Daily Papers — community-curated arXiv papers with upvotes.

URLs are emitted as arxiv.org/abs/<id>, so they merge with the plain arXiv source and
the upvotes act as the engagement signal for that paper.
"""

from __future__ import annotations

from onyourtips.core.models import Engagement, RawItem, SourceKind
from onyourtips.core.utils import parse_datetime
from onyourtips.ingestion.sources.base import Source, register

API = "https://huggingface.co/api/daily_papers"


@register
class HFPapersSource(Source):
    kind = SourceKind.HF_PAPERS

    async def fetch(self) -> list[RawItem]:
        data = await self.http.get_json(API, params={"limit": self.config.limit})
        items: list[RawItem] = []
        for entry in data if isinstance(data, list) else []:
            paper = entry.get("paper") or {}
            pid = paper.get("id")
            title = paper.get("title") or entry.get("title")
            if not pid or not title:
                continue
            authors = [a.get("name") for a in paper.get("authors", []) if a.get("name")]
            author = ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else "")
            items.append(
                self.item(
                    external_id=pid,
                    url=f"https://arxiv.org/abs/{pid}",
                    title=title,
                    summary=paper.get("summary") or entry.get("summary"),
                    author=author or None,
                    published_at=parse_datetime(
                        entry.get("publishedAt") or paper.get("publishedAt")
                    ),
                    discussion_url=f"https://huggingface.co/papers/{pid}",
                    engagement=Engagement(
                        points=paper.get("upvotes"), comments=entry.get("numComments")
                    ),
                    tags=list(paper.get("ai_keywords") or [])[:10],
                )
            )
        return items
