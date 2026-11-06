"""Newly created GitHub repositories gaining stars fast (a stable stand-in for "trending").

Uses the official search API rather than scraping github.com/trending.

params:
  created_within_days: only repos created in the last N days (default 7)
  min_stars:           minimum star count (default 50)
  topic:               optional GitHub topic filter, e.g. llm
  language:            optional language filter, e.g. python
"""

from __future__ import annotations

from datetime import timedelta

from onyourtips.core.models import Engagement, RawItem, SourceKind
from onyourtips.core.utils import parse_datetime, utcnow
from onyourtips.ingestion.sources.base import Source, register

API = "https://api.github.com/search/repositories"


@register
class GitHubSource(Source):
    kind = SourceKind.GITHUB

    def build_query(self) -> str:
        days = int(self.params.get("created_within_days", 7))
        since = (utcnow() - timedelta(days=days)).date().isoformat()
        parts = [f"created:>={since}", f"stars:>={int(self.params.get('min_stars', 50))}"]
        if topic := self.params.get("topic"):
            parts.append(f"topic:{topic}")
        if language := self.params.get("language"):
            parts.append(f"language:{language}")
        return " ".join(parts)

    async def fetch(self) -> list[RawItem]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if self.settings.github_token:
            headers["Authorization"] = f"Bearer {self.settings.github_token}"
        params = {
            "q": self.build_query(),
            "sort": "stars",
            "order": "desc",
            "per_page": min(self.config.limit, 100),
        }
        data = await self.http.get_json(API, params=params, headers=headers)

        items: list[RawItem] = []
        for repo in data.get("items", []):
            if repo.get("fork") or repo.get("archived"):
                continue
            language = repo.get("language")
            items.append(
                self.item(
                    external_id=str(repo["id"]),
                    url=repo["html_url"],
                    title=repo["full_name"],
                    summary=repo.get("description"),
                    author=(repo.get("owner") or {}).get("login"),
                    published_at=parse_datetime(repo.get("created_at")),
                    engagement=Engagement(stars=repo.get("stargazers_count")),
                    tags=list(repo.get("topics") or []) + ([language] if language else []),
                    extra={
                        "language": language,
                        "forks": repo.get("forks_count"),
                        "homepage": repo.get("homepage") or None,
                    },
                )
            )
        return items
