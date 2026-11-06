"""Ingestion pipeline: fetch (concurrent) -> normalize -> tag -> dedupe/merge -> store.

Everything after fetch is deterministic: the same RawItems always yield the same stored rows.
A failing source never stops the run; its error is recorded in ``ingestion_runs``.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta

from onyourtips.core.http import HttpClient, build_http_client
from onyourtips.core.models import (
    Article,
    RawItem,
    RunReport,
    Sighting,
    SourceConfig,
    SourceRunResult,
    UpsertOutcome,
)
from onyourtips.core.settings import Settings
from onyourtips.core.utils import unique, utcnow
from onyourtips.ingestion.normalize import (
    article_id,
    canonicalize_url,
    clean_text,
    html_to_text,
    infer_content_type,
    title_fingerprint,
)
from onyourtips.ingestion.sources import build_source
from onyourtips.ingestion.tagging import Tagger
from onyourtips.storage import Database

log = logging.getLogger(__name__)


def normalize(
    raw: RawItem, cfg: SourceConfig, tagger: Tagger, now: datetime, summary_max_chars: int = 600
) -> tuple[Article, Sighting] | None:
    url = raw.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        return None
    title = clean_text(raw.title, max_len=300)
    if not title:
        return None
    canonical = canonicalize_url(url)
    summary = html_to_text(raw.summary, max_len=summary_max_chars) or None
    source_tags = unique(raw.tags)
    published = raw.published_at if raw.published_at and raw.published_at <= now else None

    article = Article(
        id=article_id(canonical),
        canonical_url=canonical,
        url=url,
        title=title,
        title_fp=title_fingerprint(title),
        summary=summary,
        author=clean_text(raw.author, max_len=200) or None,
        content_type=infer_content_type(canonical, cfg.content_type),
        published_at=published,
        first_seen_at=now,
        last_seen_at=now,
        topics=tagger.tag(title, summary, source_tags),
        source_tags=source_tags,
        primary_source_id=cfg.id,
        extra={k: v for k, v in raw.extra.items() if v is not None},
    )
    sighting = Sighting(
        source_id=cfg.id,
        external_id=raw.external_id,
        discussion_url=raw.discussion_url,
        engagement=raw.engagement,
        seen_at=now,
    )
    return article, sighting


class Pipeline:
    def __init__(
        self,
        settings: Settings,
        sources: list[SourceConfig],
        tagger: Tagger,
        db: Database,
    ) -> None:
        self.settings = settings
        self.sources = sources
        self.tagger = tagger
        self.db = db
        self.weights = {s.id: s.weight for s in sources}

    async def run(self, only: set[str] | None = None, http: HttpClient | None = None) -> RunReport:
        selected = [s for s in self.sources if (s.id in only if only else s.enabled)]
        if only and (unknown := only - {s.id for s in self.sources}):
            raise ValueError(f"unknown source ids: {sorted(unknown)}")

        run_id = uuid.uuid4().hex[:12]
        started = utcnow()
        if http is None:
            async with build_http_client(self.settings) as client:
                fetched = await self._fetch_all(selected, client)
        else:
            fetched = await self._fetch_all(selected, http)

        results = [self._store(cfg, items, err, dur) for cfg, items, err, dur in fetched]
        finished = utcnow()
        self.db.record_run(run_id, started, finished, results)
        return RunReport(run_id=run_id, started_at=started, finished_at=finished, results=results)

    async def _fetch_all(self, configs: list[SourceConfig], http: HttpClient):
        sem = asyncio.Semaphore(self.settings.concurrency)

        async def one(cfg: SourceConfig):
            async with sem:
                t0 = time.perf_counter()
                try:
                    items = await build_source(cfg, http, self.settings).fetch()
                    return cfg, items, None, time.perf_counter() - t0
                except Exception as exc:  # isolate failures per source
                    log.warning("source %s failed: %s", cfg.id, exc)
                    return cfg, [], f"{type(exc).__name__}: {exc}", time.perf_counter() - t0

        return await asyncio.gather(*(one(c) for c in configs))

    def _store(
        self, cfg: SourceConfig, items: list[RawItem], error: str | None, duration: float
    ) -> SourceRunResult:
        result = SourceRunResult(
            source_id=cfg.id,
            ok=error is None,
            error=error,
            fetched=len(items),
            duration_s=round(duration, 2),
        )
        now = utcnow()
        cutoff = now - timedelta(days=self.settings.max_age_days)
        for raw in items:
            if raw.published_at and raw.published_at < cutoff:
                result.skipped += 1
                continue
            normalized = normalize(raw, cfg, self.tagger, now, self.settings.summary_max_chars)
            if normalized is None:
                result.skipped += 1
                continue
            outcome = self.db.upsert(
                *normalized,
                weights=self.weights,
                title_window_days=self.settings.title_dedupe_window_days,
            )
            if outcome is UpsertOutcome.NEW:
                result.new += 1
            elif outcome is UpsertOutcome.MERGED:
                result.merged += 1
            else:
                result.updated += 1
        return result
