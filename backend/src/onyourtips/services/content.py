"""Use cases for full-text extraction: which articles to extract, run it, read it back."""

from __future__ import annotations

import asyncio

import httpx
from pydantic import BaseModel

from onyourtips.core.models import ArticleContent, Passage
from onyourtips.core.settings import Settings
from onyourtips.enrichment.extraction import Extractor
from onyourtips.services.digest import build_digest
from onyourtips.storage import Database


class ContentView(BaseModel):
    content: ArticleContent
    passages: list[Passage]


class ExtractResult(BaseModel):
    article_id: str
    title: str
    status: str
    extractor: str
    words: int
    passages: int
    note: str | None = None
    cached: bool = False  # already extracted earlier; nothing fetched


def select_for_extraction(
    db: Database,
    settings: Settings,
    *,
    ids: list[str] | None = None,
    digest: bool = False,
    saved: bool = False,
) -> list[str]:
    """Only selected articles get extracted (ADR 0002): explicit ids, the weekly digest, saved."""
    chosen: list[str] = []
    for i in ids or []:
        chosen.append(db.resolve_id(i))
    if digest:
        chosen += [item.article.id for item in build_digest(db, settings)]
    if saved:
        chosen += [item.article.id for item in db.saved()]
    return list(dict.fromkeys(chosen))  # de-duplicate, keep order


async def extract_articles(
    db: Database,
    settings: Settings,
    article_ids: list[str],
    *,
    force: bool = False,
    http: httpx.AsyncClient | None = None,
) -> list[ExtractResult]:
    done = set() if force else db.extracted_ids(article_ids)
    results: list[ExtractResult] = []
    todo = []
    for aid in article_ids:
        art = db.get_article(aid)
        if art is None:
            continue
        if aid in done:
            c = db.latest_content(aid)
            results.append(
                ExtractResult(
                    article_id=aid,
                    title=art.title,
                    status=c.status.value,
                    extractor=c.extractor,
                    words=c.word_count,
                    passages=len(db.get_passages(c.content_hash)) if c.content_hash else 0,
                    note=c.error,
                    cached=True,
                )
            )
        else:
            todo.append(art)

    async def run(client: httpx.AsyncClient) -> None:
        extractor = Extractor(settings, client)
        sem = asyncio.Semaphore(settings.extract_concurrency)

        async def one(art):
            async with sem:
                return art, await extractor.extract(art)

        for art, ex in await asyncio.gather(*(one(a) for a in todo)):
            db.save_content(ex.content, ex.passages)
            results.append(
                ExtractResult(
                    article_id=art.id,
                    title=art.title,
                    status=ex.content.status.value,
                    extractor=ex.content.extractor,
                    words=ex.content.word_count,
                    passages=len(ex.passages),
                    note=ex.content.error,
                )
            )

    if todo:
        if http is not None:
            await run(http)
        else:
            async with httpx.AsyncClient(
                timeout=settings.http_timeout,
                follow_redirects=True,
                headers={"User-Agent": settings.user_agent},
            ) as client:
                await run(client)
    order = {aid: i for i, aid in enumerate(article_ids)}
    return sorted(results, key=lambda r: order.get(r.article_id, 0))


def get_content(db: Database, article_id: str) -> ContentView | None:
    try:
        full_id = db.resolve_id(article_id)
    except (KeyError, ValueError):
        return None
    content = db.latest_content(full_id)
    if content is None:
        return None
    passages = db.get_passages(content.content_hash) if content.content_hash else []
    return ContentView(content=content, passages=passages)
